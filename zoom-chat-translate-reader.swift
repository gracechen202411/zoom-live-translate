// 读取 Zoom「网络研讨会聊天 / 会议聊天」窗口里的消息，每条一行 JSON 输出到 stdout。
// 由 zoom-chat-translate.sh 调用，下游 zoom-chat-translate.py 去重 + 翻译。
//
// 原理：Zoom 聊天列表是网页内核渲染的，默认不暴露文字；给 Zoom 进程打开
// AXManualAccessibility 后，聊天表格每一行的 AXDescription 就是
// 「发送人发给接收人, 消息内容, HH:MM」。列表是虚拟滚动的，只能读到当前可见的行，
// 所以聊天窗口要停在最底部，新消息才会出现在可见区域。
// 需要「终端」在 系统设置 → 隐私与安全性 → 辅助功能 里被允许。

import AppKit
import ApplicationServices
import Foundation

let pollInterval: TimeInterval = 1.5
// 中文版 Zoom 的聊天窗口标题；英文版是 "Chat" / "Webinar Chat"
let chatWindowKeywords = ["聊天", "Chat"]
let chatTableDescriptionPrefix = "聊天列表"

func attr(_ element: AXUIElement, _ name: String) -> AnyObject? {
    var value: AnyObject?
    AXUIElementCopyAttributeValue(element, name as CFString, &value)
    return value
}

func children(_ element: AXUIElement) -> [AXUIElement] {
    (attr(element, "AXChildren") as? [AXUIElement]) ?? []
}

/// 深度优先找到聊天表格（AXTable，描述以「聊天列表」开头）。
func findChatTable(_ element: AXUIElement, depth: Int = 0) -> AXUIElement? {
    if depth > 12 { return nil }
    if (attr(element, "AXRole") as? String) == "AXTable",
       let desc = attr(element, "AXDescription") as? String,
       desc.hasPrefix(chatTableDescriptionPrefix) || desc.lowercased().hasPrefix("chat") {
        return element
    }
    for child in children(element) {
        if let table = findChatTable(child, depth: depth + 1) { return table }
    }
    return nil
}

/// 聊天表格的每一行（表格 → 行容器 → 行），取带「发给」或 "to" 的那层描述。
func messageRows(in table: AXUIElement) -> [String] {
    var rows: [String] = []
    func walk(_ element: AXUIElement, _ depth: Int) {
        if depth > 3 { return }
        if let desc = attr(element, "AXDescription") as? String,
           desc.contains("发给") || desc.range(of: " to ", options: .caseInsensitive) != nil,
           desc.contains(", ") {
            rows.append(desc)
            return
        }
        for child in children(element) { walk(child, depth + 1) }
    }
    for child in children(table) { walk(child, 1) }
    return rows
}

func emit(_ payload: [String: String]) {
    guard let data = try? JSONSerialization.data(withJSONObject: payload),
          let line = String(data: data, encoding: .utf8) else { return }
    FileHandle.standardOutput.write((line + "\n").data(using: .utf8)!)
}

guard AXIsProcessTrusted() else {
    emit(["error": "终端没有辅助功能权限：系统设置 → 隐私与安全性 → 辅助功能 → 打开「终端」"])
    exit(1)
}

var announcedMissing = false
while true {
    guard let zoom = NSRunningApplication.runningApplications(withBundleIdentifier: "us.zoom.xos").first else {
        if !announcedMissing { emit(["status": "等待 Zoom 启动……"]); announcedMissing = true }
        Thread.sleep(forTimeInterval: 3)
        continue
    }
    let app = AXUIElementCreateApplication(zoom.processIdentifier)
    // 每轮都设一次：Zoom 重开聊天窗口后会重置
    AXUIElementSetAttributeValue(app, "AXManualAccessibility" as CFString, kCFBooleanTrue)

    let windows = (attr(app, "AXWindows") as? [AXUIElement]) ?? []
    let chatWindows = windows.filter { window in
        let title = (attr(window, "AXTitle") as? String) ?? ""
        return chatWindowKeywords.contains { title.contains($0) }
    }
    var foundTable = false
    for window in chatWindows {
        guard let table = findChatTable(window) else { continue }
        foundTable = true
        announcedMissing = false
        for row in messageRows(in: table) { emit(["row": row]) }
    }
    if !foundTable && !announcedMissing {
        emit(["status": "没找到 Zoom 聊天窗口，请在 Zoom 里点「聊天」把它打开（建议弹出成独立窗口）"])
        announcedMissing = true
    }
    Thread.sleep(forTimeInterval: pollInterval)
}
