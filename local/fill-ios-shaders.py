#!/usr/bin/env python3
"""Prepare only the shaders this iPhone requested, then return their pack to the same iPhone."""
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import runpy
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
from common import add_paths, load_identity, tree_python

TREE = None
PRIVATE = None
FILL = None


class HelperError(RuntimeError):
    pass


def read_json(path):
    return json.loads(path.read_text())


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def run_logged(label, command, env, run_dir, timeout=180):
    log = run_dir / (label + '.log')
    try:
        with log.open('w') as output:
            result = subprocess.run([str(value) for value in command], env=env,
                                    stdout=output, stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise HelperError(f'Истекло время ожидания: {label}. Подробности: {log}') from error
    if result.returncode:
        raise HelperError(f'Не удалось выполнить {label}, код {result.returncode}. Подробности: {log}')
    return log


def device_json(label, arguments, env, run_dir):
    result_path = run_dir / (label + '.json')
    run_logged(label, ['xcrun', 'devicectl', *arguments, '--json-output', result_path], env, run_dir)
    if not result_path.is_file():
        raise HelperError(f'Устройство не вернуло результат {label}; это не означает отсутствие запросов.')
    result = read_json(result_path)
    if result.get('error') or result.get('info', {}).get('outcome') not in (None, 'success'):
        raise HelperError(f'Устройство вернуло ошибку {label}. Подробности: {result_path}')
    if not isinstance(result.get('result'), dict):
        raise HelperError(f'Неожиданный ответ {label}. Подробности: {result_path}')
    return result['result']


def list_files(label, subdirectory, device, bundle_id, env, run_dir):
    result = device_json(label, ['device', 'info', 'files', '--device', device,
                                '--domain-type', 'appDataContainer', '--domain-identifier', bundle_id,
                                '--subdirectory', subdirectory, '--no-recurse'], env, run_dir)
    rows = result.get('files')
    if not isinstance(rows, list):
        raise HelperError(f'Нет достоверного списка файлов {subdirectory}; проверка остановлена.')
    found = {}
    for row in rows:
        if not isinstance(row, dict):
            raise HelperError(f'Неожиданная запись в списке {subdirectory}.')
        name = row.get('relativePath') or row.get('name')
        if not isinstance(name, str) or not name:
            raise HelperError(f'Запись без имени в списке {subdirectory}.')
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts:
            raise HelperError(f'Некорректный путь в списке {subdirectory}.')
        for prefix in (subdirectory + '/', PurePosixPath(subdirectory).name + '/'):
            if name.startswith(prefix):
                name = name[len(prefix):]
                break
        if '/' in name or name in ('', '.', '..'):
            continue  # Never treat done/<variant>.json as a pending request.
        if name in found:
            raise HelperError(f'Повторное имя файла в списке {subdirectory}.')
        found[name] = row
    return found


def copy_from(label, remote, local, device, bundle_id, env, run_dir):
    run_logged(label, ['xcrun', 'devicectl', 'device', 'copy', 'from', '--device', device,
                       '--domain-type', 'appDataContainer', '--domain-identifier', bundle_id,
                       '--source', remote, '--destination', local], env, run_dir)
    if not local.is_file() or local.is_symlink():
        raise HelperError(f'Не получен обычный локальный файл: {local.name}.')


def ensure_translator():
    prebuilt = TREE / 'prebuilt/macos-arm64/totk_xlate'
    checksum = prebuilt.with_name('totk_xlate.sha256')
    if not prebuilt.is_file() or not checksum.is_file() or not os.access(prebuilt, os.X_OK):
        raise HelperError('Не найден готовый исполняемый переводчик и его контрольная сумма.')
    expected = checksum.read_text().split()[0].lower()
    if not re.fullmatch(r'[0-9a-f]{64}', expected) or sha256(prebuilt) != expected:
        raise HelperError('Контрольная сумма готового переводчика не совпала; ничего не заменено.')
    service = TREE / 'build/shader-service/totk_xlate'
    if not service.parent.resolve().is_relative_to(TREE.resolve()):
        raise HelperError('Папка shader-service ведёт за пределы рабочего дерева; ничего не заменено.')
    if not service.exists() and not service.is_symlink():
        service.parent.mkdir(parents=True, exist_ok=True)
        try:
            service.symlink_to(os.path.relpath(prebuilt, service.parent))
        except FileExistsError:
            pass  # Another launch may have just created it; validate it below.
    if service.is_symlink():
        if service.resolve() != prebuilt.resolve():
            raise HelperError('У shader-service неожиданная ссылка; она не перезаписана.')
    elif not service.is_file() or sha256(service) != expected:
        raise HelperError('В shader-service другой переводчик; он не перезаписан.')
    if not os.access(service, os.X_OK):
        raise HelperError('Проверенный переводчик не является исполняемым.')
    cross = TREE / 'build/spirv-cross/spirv-cross'
    if not cross.is_file() or not os.access(cross, os.X_OK):
        raise HelperError('Нет готового SPIRV-Cross. Сборка .NET или других инструментов не запускается.')


def main(argv=None):
    global TREE, PRIVATE, FILL
    parser = argparse.ArgumentParser(
        description='Подготовить только недостающие шейдеры TOTK и передать их на настроенный iPhone.')
    add_paths(parser, signing=True)
    parser.add_argument('--check', action='store_true',
                        help='только проверить доступ к Documents и прочитать новые JSON-запросы; без сборки и отправки')
    args = parser.parse_args(argv)  # --help never reads configuration or calls a device.
    TREE, PRIVATE = args.tree, args.logs
    FILL = TREE / 'ios/tools/fill_shaders.py'
    os.umask(0o077)
    run_dir = None
    receipt = {'mode': 'check' if args.check else 'fill', 'status': 'starting'}
    lock = None
    try:
        identity = load_identity(args, require_team=False)
        bundle_id, udid = identity['bundle_id'], identity['device_id']
        if not FILL.is_file():
            raise HelperError(f'Нет существующего помощника: {FILL}')
        PRIVATE.mkdir(parents=True, exist_ok=True)
        if not args.check:
            lock = (PRIVATE / 'totk-ios-shader-fill.lock').open('a')
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise HelperError('Подготовка шейдеров уже идёт. Дождись её завершения.') from error
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        run_dir = Path(tempfile.mkdtemp(prefix='totk-ios-shader-fill-' + stamp + '-', dir=PRIVATE))
        print('Проверяю настроенный iPhone и новые запросы шейдеров.', flush=True)
        print(f'Журнал и результат: {run_dir}', flush=True)
        sys.path.insert(0, str(FILL.parent))
        helper = runpy.run_path(str(FILL), run_name='totk_fill_read_only_preflight')
        env = dict(helper['xcode_env'](), PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1', TOTK_BUILD_JOBS=str(args.jobs))
        devices = device_json('devices', ['list', 'devices'], env, run_dir).get('devices')
        if not isinstance(devices, list):
            raise HelperError('Не получен достоверный список устройств.')
        matches = [device for device in devices
                   if (device.get('hardwareProperties', {}).get('udid') == udid or device.get('identifier') == udid)
                   and device.get('hardwareProperties', {}).get('platform') == 'iOS']
        if len(matches) != 1:
            raise HelperError('Настроенный iPhone не обнаружен однозначно. Подключи и разблокируй именно этот iPhone.')
        device = matches[0]
        if device.get('connectionProperties', {}).get('pairingState') != 'paired':
            raise HelperError('Этот iPhone не сопряжён с Mac. Доступ к приложению не проверен.')
        identifier = device.get('identifier')
        if not isinstance(identifier, str) or not identifier:
            raise HelperError('Не получен свежий идентификатор настроенного iPhone.')
        # A successful fresh Documents read is required before absence can mean "no requests".
        documents = list_files('documents', 'Documents', identifier, bundle_id, env, run_dir)
        receipt['documents_accessible'] = True
        if 'shader-requests' not in documents:
            receipt.update(status='checked' if args.check else 'no_requests',
                           request_folder_present=False, json_files=0, valid_requests=0)
            print('Доступ проверен. Папки новых запросов пока нет; подготавливать нечего.')
            return 0
        remote = list_files('requests-list', 'Documents/shader-requests', identifier, bundle_id, env, run_dir)
        names = sorted(name for name in remote if name.endswith('.json'))
        receipt.update(request_folder_present=True, json_files=len(names))
        folder = run_dir / 'requests'
        folder.mkdir()
        # Copy pending top-level JSON files only. The completed requests in done/ are never fetched or replayed.
        for index, name in enumerate(names, 1):
            metadata = remote[name].get('metadata', {})
            expected_size = metadata.get('size')
            if isinstance(expected_size, int) and expected_size > 64 * 1024 * 1024:
                raise HelperError('Слишком большой JSON-запрос; автоматическая обработка остановлена.')
            path = folder / name
            copy_from(f'request-{index:05d}', 'Documents/shader-requests/' + name,
                      path, identifier, bundle_id, env, run_dir)
            if isinstance(expected_size, int) and path.stat().st_size != expected_size:
                raise HelperError('Размер полученного запроса изменился. Повтори проверку после завершения записи файла.')
        with (run_dir / 'request-validation.log').open('w') as output, contextlib.redirect_stdout(output):
            requests = helper['load_requests'](folder)
        receipt['valid_requests'] = len(requests)
        if len(requests) != len(names):
            raise HelperError('Есть неполные или некорректные JSON-запросы; это не считается отсутствием запросов.')
        if args.check:
            receipt['status'] = 'checked'
            print(f'Доступ проверен. Новых корректных запросов: {len(requests)}. Сборка и отправка не запускались.')
            return 0
        if not requests:
            receipt['status'] = 'no_requests'
            print('Новых запросов нет. Подготовленные ранее запросы не обрабатывались.')
            return 0
        ensure_translator()
        work = run_dir / 'work'
        work.mkdir()
        env['TMPDIR'] = str(work)
        receipt['requested_build_jobs'] = args.jobs
        print(f'Подготавливаю только запрошенные варианты: {len(requests)}.', flush=True)
        log = run_logged('fill', [tree_python(TREE), FILL, '--tree', TREE, '--device', identifier,
                                  '--bundle-id', bundle_id, '--requests', folder], env, run_dir, timeout=None)
        text = log.read_text(errors='replace')
        match = re.search(r'^pack ([0-9]{8}-[0-9]{6}): ([0-9]+) variants, ([0-9]+) files(?:, ([0-9]+) failed)?$', text, re.M)
        if not match or 'pushed; restart the game on the device' not in text:
            raise HelperError('Помощник завершился без подтверждения отправки пакета. Проверь fill.log.')
        name, variants, files, failed = match.groups()
        variants, files, failed = int(variants), int(files), int(failed or 0)
        if variants + failed != len(requests):
            raise HelperError('Число подготовленных вариантов не совпало с проверенными запросами.')
        # Independent readback of the uploaded package; exit code alone is not the delivery receipt.
        uploaded = list_files('pushed-pack-files', 'Documents/shader-fill/' + name,
                              identifier, bundle_id, env, run_dir)
        manifest_path = run_dir / 'pushed-manifest.json'
        copy_from('pushed-manifest', 'Documents/shader-fill/' + name + '/seed-manifest.json',
                  manifest_path, identifier, bundle_id, env, run_dir)
        manifest = read_json(manifest_path)
        hashes = manifest.get('files')
        if (manifest.get('schema') != 1 or manifest.get('chain') != helper['chain']()
                or manifest.get('shaders') != variants or not isinstance(hashes, dict) or len(hashes) != files
                or 'seed-manifest.json' not in uploaded):
            raise HelperError('Прочитанный с iPhone манифест пакета не прошёл проверку.')
        if any(Path(filename).name != filename or filename not in uploaded
               or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
               for filename, digest in hashes.items()):
            raise HelperError('Не все файлы отправленного пакета подтверждены на iPhone.')
        receipt.update(status='partially_pushed' if failed else 'pushed', pack=name,
                       pushed_variants=variants, failed_requests=failed, remote_manifest_verified=True,
                       restart_required=True)
        print(f'Пакет передан на этот iPhone: {variants} вариантов. Манифест и наличие файлов проверены.')
        if failed:
            print(f'Не удалось подготовить запросов: {failed}. Подробности в fill.log.')
        print('Закрой и заново открой TOTK на iPhone, чтобы применить пакет. Игра автоматически не перезапускалась.')
        return 2 if failed else 0
    except (HelperError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        receipt.update(status='error', error=str(error))
        print(f'Остановлено: {error}', file=sys.stderr)
        if run_dir:
            print(f'Журнал и результат: {run_dir}', file=sys.stderr)
        return 1
    finally:
        if run_dir:
            (run_dir / 'result.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
        if lock:
            lock.close()


if __name__ == '__main__':
    sys.exit(main())
