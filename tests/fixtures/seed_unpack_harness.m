
#import <Foundation/Foundation.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
static BOOL failAlloc, failFlush;
static void *test_malloc(size_t n) { return failAlloc ? NULL : malloc(n); }
static int test_fclose(FILE *f) { int r=fclose(f); if (failFlush) { failFlush=NO; return EOF; } return r; }
#define malloc test_malloc
#define fclose test_fclose
__PRODUCTION__
#undef malloc
#undef fclose

static void check(BOOL b, const char *why) { if (!b) { fprintf(stderr,"FAIL: %s\n",why); exit(1); } }
static NSMutableData *pack(NSString *name, NSData *content) {
    NSMutableData *d=[NSMutableData dataWithBytes:"TOTKPACK1" length:9];
    NSData *n=[name dataUsingEncoding:NSUTF8StringEncoding]; uint16_t nl=n.length; uint64_t size=content.length, zero=0;
    [d appendBytes:&nl length:2]; [d appendData:n]; [d appendBytes:&size length:8]; [d appendData:content]; [d appendBytes:&zero length:2];
    return d;
}
static void runCase(NSString *root, NSString *label, NSMutableData *data, BOOL split, BOOL oom, BOOL flush, BOOL expectOK) {
    NSFileManager *fm=NSFileManager.defaultManager;
    NSString *dir=[root stringByAppendingPathComponent:label]; [fm createDirectoryAtPath:dir withIntermediateDirectories:YES attributes:nil error:nil];
    NSString *base=[dir stringByAppendingPathComponent:@"seed.pack"], *dest=[dir stringByAppendingPathComponent:@"seed"];
    [fm createDirectoryAtPath:dest withIntermediateDirectories:YES attributes:nil error:nil];
    [@"keep" writeToFile:[dest stringByAppendingPathComponent:@"old"] atomically:YES encoding:NSUTF8StringEncoding error:nil];
    NSUInteger part=0;
    for (NSUInteger at=0; at<data.length;) { NSUInteger n=split ? MIN((NSUInteger)7,data.length-at) : data.length-at;
        check([[data subdataWithRange:NSMakeRange(at,n)] writeToFile:[base stringByAppendingFormat:@".%03lu",(unsigned long)part++] atomically:YES],"fixture write"); at+=n; }
    failAlloc=oom;failFlush=flush;
    NSString *why=seed_unpack(base,dest,nil); failAlloc=failFlush=NO;
    check((why==nil)==expectOK,"unpack status");
    check(![fm fileExistsAtPath:[dest stringByAppendingString:@".tmp"]],"no incomplete committed seed");
    if (expectOK) {
        check([[NSData dataWithContentsOfFile:[dest stringByAppendingPathComponent:@"shader.metal"]] isEqualToData:[@"shader payload" dataUsingEncoding:NSUTF8StringEncoding]],"exact payload across parts");
        check(![fm fileExistsAtPath:[base stringByAppendingString:@".000"]],"successful packs removed");
    } else {
        check([[NSString stringWithContentsOfFile:[dest stringByAppendingPathComponent:@"old"] encoding:NSUTF8StringEncoding error:nil] isEqualToString:@"keep"],"previous valid seed preserved");
        check([fm fileExistsAtPath:[base stringByAppendingString:@".000"]],"failed packs retained for retry");
    }
    printf("PASS %s\n",label.UTF8String);
}
int main(void) { @autoreleasepool {
    NSString *root=[NSTemporaryDirectory() stringByAppendingPathComponent:NSUUID.UUID.UUIDString];
    NSData *content=[@"shader payload" dataUsingEncoding:NSUTF8StringEncoding];
    NSMutableData *valid=pack(@"shader.metal",content);
    runCase(root,@"valid_cross_part",valid,YES,NO,NO,YES);
    NSMutableData *truncated=[valid mutableCopy];truncated.length-=3;
    runCase(root,@"truncated_stream",truncated,NO,NO,NO,NO);
    NSMutableData *bad=[valid mutableCopy];((uint8_t *)bad.mutableBytes)[0]='X';
    runCase(root,@"bad_magic",bad,NO,NO,NO,NO);
    runCase(root,@"unsafe_entry",pack(@"../escape",content),NO,NO,NO,NO);
    runCase(root,@"allocation_failure",valid,NO,YES,NO,NO);
    runCase(root,@"final_flush_failure",valid,NO,NO,YES,NO);
    [NSFileManager.defaultManager removeItemAtPath:root error:nil];
} return 0; }
