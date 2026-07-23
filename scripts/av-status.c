/* av-status — report whether the microphone or camera is in active use.
 *
 * Prints one line, e.g. "mic=1 cam=0", and exits 0. These are the same
 * signals macOS uses for the orange (mic) and green (camera) menu-bar
 * dots, so any app counts: Zoom, Teams, FaceTime, a browser tab on Meet.
 * Reading these properties needs no mic/camera TCC permission.
 *
 * Built by install.sh:
 *   cc -O2 -o ~/.claude/scripts/av-status scripts/av-status.c \
 *      -framework CoreAudio -framework CoreMediaIO -framework CoreFoundation
 */
#include <CoreAudio/CoreAudio.h>
#include <CoreMediaIO/CMIOHardware.h>
#include <stdio.h>
#include <stdlib.h>

/* Process-level input state (macOS 14+). Define the four-char selectors
 * ourselves so this also compiles against older SDK headers. */
#ifndef kAudioHardwarePropertyProcessObjectList
#define kAudioHardwarePropertyProcessObjectList 'prs#'
#endif
#ifndef kAudioProcessPropertyIsRunningInput
#define kAudioProcessPropertyIsRunningInput 'piri'
#endif

/* Mic, preferred check: does ANY process currently have input running?
 * Exact orange-dot semantics. Returns -1 if the API is unavailable. */
static int mic_running_by_process(void) {
    AudioObjectPropertyAddress addr = {
        kAudioHardwarePropertyProcessObjectList,
        kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
    UInt32 size = 0;
    if (AudioObjectGetPropertyDataSize(kAudioObjectSystemObject, &addr, 0,
                                       NULL, &size) != noErr || size == 0)
        return -1;
    UInt32 n = size / sizeof(AudioObjectID);
    AudioObjectID *procs = malloc(size);
    if (!procs) return -1;
    if (AudioObjectGetPropertyData(kAudioObjectSystemObject, &addr, 0, NULL,
                                   &size, procs) != noErr) {
        free(procs);
        return -1;
    }
    int running = 0;
    for (UInt32 i = 0; i < n && !running; i++) {
        AudioObjectPropertyAddress p = {
            kAudioProcessPropertyIsRunningInput,
            kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
        UInt32 val = 0, vsize = sizeof(val);
        if (AudioObjectGetPropertyData(procs[i], &p, 0, NULL, &vsize, &val) ==
                noErr && val)
            running = 1;
    }
    free(procs);
    return running;
}

/* Mic, fallback for pre-Sonoma: any device WITH INPUT STREAMS running
 * somewhere. Duplex devices (AirPods) playing output can false-positive
 * here, which errs toward staying quiet — acceptable for a fallback. */
static int mic_running_by_device(void) {
    AudioObjectPropertyAddress addr = {
        kAudioHardwarePropertyDevices,
        kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
    UInt32 size = 0;
    if (AudioObjectGetPropertyDataSize(kAudioObjectSystemObject, &addr, 0,
                                       NULL, &size) != noErr || size == 0)
        return 0;
    UInt32 n = size / sizeof(AudioDeviceID);
    AudioDeviceID *devs = malloc(size);
    if (!devs) return 0;
    if (AudioObjectGetPropertyData(kAudioObjectSystemObject, &addr, 0, NULL,
                                   &size, devs) != noErr) {
        free(devs);
        return 0;
    }
    int running = 0;
    for (UInt32 i = 0; i < n && !running; i++) {
        AudioObjectPropertyAddress streams = {
            kAudioDevicePropertyStreams,
            kAudioDevicePropertyScopeInput, kAudioObjectPropertyElementMain};
        UInt32 ssize = 0;
        if (AudioObjectGetPropertyDataSize(devs[i], &streams, 0, NULL,
                                           &ssize) != noErr || ssize == 0)
            continue;
        AudioObjectPropertyAddress somewhere = {
            kAudioDevicePropertyDeviceIsRunningSomewhere,
            kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
        UInt32 val = 0, vsize = sizeof(val);
        if (AudioObjectGetPropertyData(devs[i], &somewhere, 0, NULL, &vsize,
                                       &val) == noErr && val)
            running = 1;
    }
    free(devs);
    return running;
}

/* Camera: any CoreMediaIO device running somewhere — green-light semantics. */
static int cam_running(void) {
    CMIOObjectPropertyAddress addr = {
        kCMIOHardwarePropertyDevices,
        kCMIOObjectPropertyScopeGlobal, kCMIOObjectPropertyElementMain};
    UInt32 size = 0;
    if (CMIOObjectGetPropertyDataSize(kCMIOObjectSystemObject, &addr, 0,
                                      NULL, &size) != noErr || size == 0)
        return 0;
    UInt32 n = size / sizeof(CMIODeviceID);
    CMIODeviceID *devs = malloc(size);
    if (!devs) return 0;
    UInt32 used = 0;
    if (CMIOObjectGetPropertyData(kCMIOObjectSystemObject, &addr, 0, NULL,
                                  size, &used, devs) != noErr) {
        free(devs);
        return 0;
    }
    int running = 0;
    for (UInt32 i = 0; i < n && !running; i++) {
        CMIOObjectPropertyAddress p = {
            kCMIODevicePropertyDeviceIsRunningSomewhere,
            kCMIOObjectPropertyScopeGlobal, kCMIOObjectPropertyElementMain};
        UInt32 val = 0, vsize = 0;
        if (CMIOObjectGetPropertyData(devs[i], &p, 0, NULL, sizeof(val),
                                      &vsize, &val) == noErr && val)
            running = 1;
    }
    free(devs);
    return running;
}

int main(void) {
    int mic = mic_running_by_process();
    if (mic < 0)
        mic = mic_running_by_device();
    printf("mic=%d cam=%d\n", mic ? 1 : 0, cam_running() ? 1 : 0);
    return 0;
}
