// 创建「Zoom+字幕」多输出设备（扬声器 + BlackHole 2ch）并设为系统默认输出。
// 这样 Zoom 的声音既能听到，又能被 whisper-stream 从 BlackHole 抓到。
// 用法：swift scripts/zoom-live-translate-audio-setup.swift [restore]
//   restore = 把系统输出切回内置扬声器（下课后可选）
// 扬声器默认自动找 Mac 内置扬声器；想用别的（如外接音箱）设环境变量 ZOOM_SPEAKER_NAME=设备名

import CoreAudio
import Foundation

let aggregateName = "Zoom+字幕"
let blackholeName = "BlackHole 2ch"

func propertyAddress(_ selector: AudioObjectPropertySelector) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
}

func allDevices() -> [AudioDeviceID] {
    var addr = propertyAddress(kAudioHardwarePropertyDevices)
    var size: UInt32 = 0
    AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size)
    var ids = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
    AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &ids)
    return ids
}

func stringProperty(_ id: AudioDeviceID, _ selector: AudioObjectPropertySelector) -> String {
    var addr = propertyAddress(selector)
    var value: Unmanaged<CFString>?
    var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    guard AudioObjectGetPropertyData(id, &addr, 0, nil, &size, &value) == noErr, let v = value else { return "" }
    return v.takeRetainedValue() as String
}

func findDevice(named name: String) -> AudioDeviceID? {
    allDevices().first { stringProperty($0, kAudioObjectPropertyName) == name }
}

func hasOutputStreams(_ id: AudioDeviceID) -> Bool {
    var addr = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyStreams, mScope: kAudioObjectPropertyScopeOutput, mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    AudioObjectGetPropertyDataSize(id, &addr, 0, nil, &size)
    return size > 0
}

/// 优先用 ZOOM_SPEAKER_NAME 指定的设备，否则找内置扬声器（设备名随系统语言变，所以按类型找）
func resolveSpeaker() -> AudioDeviceID? {
    if let name = ProcessInfo.processInfo.environment["ZOOM_SPEAKER_NAME"] { return findDevice(named: name) }
    return allDevices().first { id in
        var addr = propertyAddress(kAudioDevicePropertyTransportType)
        var transport: UInt32 = 0
        var size = UInt32(MemoryLayout<UInt32>.size)
        AudioObjectGetPropertyData(id, &addr, 0, nil, &size, &transport)
        return transport == kAudioDeviceTransportTypeBuiltIn && hasOutputStreams(id)
    }
}

func setDefaultOutput(_ id: AudioDeviceID) {
    var addr = propertyAddress(kAudioHardwarePropertyDefaultOutputDevice)
    var dev = id
    let status = AudioObjectSetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, UInt32(MemoryLayout<AudioDeviceID>.size), &dev)
    print(status == noErr ? "✅ 系统输出已切到「\(stringProperty(id, kAudioObjectPropertyName))」" : "❌ 切换默认输出失败 \(status)")
}

if CommandLine.arguments.contains("restore") {
    guard let speaker = resolveSpeaker() else { print("❌ 找不到扬声器，可用 ZOOM_SPEAKER_NAME 指定"); exit(1) }
    setDefaultOutput(speaker)
    exit(0)
}

guard let speaker = resolveSpeaker() else { print("❌ 找不到扬声器，可用 ZOOM_SPEAKER_NAME 指定"); exit(1) }
let speakerName = stringProperty(speaker, kAudioObjectPropertyName)
guard let blackhole = findDevice(named: blackholeName) else { print("❌ 找不到 \(blackholeName)，请确认已安装并重启"); exit(1) }

if let existing = findDevice(named: aggregateName) {
    print("ℹ️ 「\(aggregateName)」已存在，直接启用")
    setDefaultOutput(existing)
    exit(0)
}

let speakerUID = stringProperty(speaker, kAudioDevicePropertyDeviceUID)
let blackholeUID = stringProperty(blackhole, kAudioDevicePropertyDeviceUID)

// 多输出设备 = 带 stacked 标记的聚合设备；扬声器作主时钟，BlackHole 做副输出
let description: [String: Any] = [
    kAudioAggregateDeviceNameKey: aggregateName,
    kAudioAggregateDeviceUIDKey: "io.github.zoom-live-translate.multi-output",
    kAudioAggregateDeviceIsStackedKey: 1,
    kAudioAggregateDeviceMainSubDeviceKey: speakerUID,
    kAudioAggregateDeviceSubDeviceListKey: [
        [kAudioSubDeviceUIDKey: speakerUID],
        [kAudioSubDeviceUIDKey: blackholeUID, kAudioSubDeviceDriftCompensationKey: 1],
    ],
]

var aggregateID: AudioDeviceID = 0
let status = AudioHardwareCreateAggregateDevice(description as CFDictionary, &aggregateID)
guard status == noErr else { print("❌ 创建多输出设备失败 \(status)"); exit(1) }
print("✅ 已创建多输出设备「\(aggregateName)」= \(speakerName) + \(blackholeName)")
usleep(500_000) // 等 CoreAudio 注册完再设默认
setDefaultOutput(aggregateID)
