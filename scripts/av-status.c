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
#include <CoreFoundation/CoreFoundation.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

/* Process-level input state (macOS 14+). Define the four-char selectors
 * ourselves so this also compiles against older SDK headers. */
#ifndef kAudioHardwarePropertyProcessObjectList
#define kAudioHardwarePropertyProcessObjectList 'prs#'
#endif
#ifndef kAudioProcessPropertyIsRunningInput
#define kAudioProcessPropertyIsRunningInput 'piri'
#endif
#ifndef kAudioProcessPropertyBundleID
#define kAudioProcessPropertyBundleID 'pbid'
#endif

/* Bundle-ID prefixes that hold the mic open purely to listen for TEXT INPUT
 * (Dictation, Voice Control, Siri's on-device recognizer) rather than for a
 * call. The first two were confirmed empirically (2026-08-02: with Voice
 * Control on and no call running, the only two processes with input
 * running were com.apple.SpeechRecognitionCore.speechrecognitiond and
 * com.apple.inputmethod.ironwood — no conferencing app anywhere). Without
 * this exclusion, Voice Control's own listening makes on_call()
 * permanently true and claude-speaker never speaks again. A real
 * Zoom/Teams/FaceTime/browser call still shows its own distinct bundle ID
 * and is unaffected.
 *
 * These are BUNDLE IDs, which are not the daemon's executable name — a
 * distinction worth stating because it already bit this list once. The
 * CoreSpeech daemon runs as `corespeechd`, but the bundle ID this code
 * compares against is com.apple.CoreSpeech, so a "com.apple.corespeechd"
 * entry silently matches nothing. Every prefix below was read back off a
 * live kAudioProcessPropertyBundleID enumeration (2026-08-03) rather than
 * guessed from a process listing. com.apple.accessibility covers Voice
 * Control's own `heard` daemon (com.apple.accessibility.heard), which is
 * the single most on-the-nose candidate for the bug this fixes and was
 * missing entirely. Matching is case-insensitive because Apple's own
 * casing in this namespace is not consistent (CoreSpeech vs inputmethod). */
static const char *DICTATION_INFRA[] = {
    "com.apple.SpeechRecognitionCore",
    "com.apple.inputmethod",
    "com.apple.CoreSpeech",
    "com.apple.accessibility",
    "com.apple.universalaccessd",
    "com.apple.assistant",
    "com.apple.Siri",
    NULL};

static int is_dictation_infra(AudioObjectID proc_obj) {
    AudioObjectPropertyAddress bp = {
        kAudioProcessPropertyBundleID,
        kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
    CFStringRef bundle_id = NULL;
    UInt32 bsize = sizeof(bundle_id);
    if (AudioObjectGetPropertyData(proc_obj, &bp, 0, NULL, &bsize,
                                   &bundle_id) != noErr || !bundle_id)
        return 0;
    char buf[256] = "";
    CFStringGetCString(bundle_id, buf, sizeof(buf), kCFStringEncodingUTF8);
    CFRelease(bundle_id);
    if (!buf[0]) return 0;  /* unreadable: treat as a real mic user */
    for (int i = 0; DICTATION_INFRA[i]; i++)
        if (strncasecmp(buf, DICTATION_INFRA[i],
                        strlen(DICTATION_INFRA[i])) == 0)
            return 1;
    return 0;
}

/* Mic, preferred check: does ANY process currently have input running,
 * OTHER than known dictation/speech-recognition infrastructure?
 * Exact orange-dot semantics, minus that one carve-out. Returns -1 if the
 * API is unavailable. */
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
                noErr && val && !is_dictation_infra(procs[i]))
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
