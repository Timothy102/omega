import Foundation
import Testing
@testable import OmegaCore

@Test func decodesTextDelta() throws {
    let json = #"{"type":"TextDelta","t":1.0,"text":"hello"}"#.data(using: .utf8)!
    let event = try JSONDecoder().decode(Event.self, from: json)
    guard case .textDelta(let d) = event.payload else { Issue.record("wrong case"); return }
    #expect(d.text == "hello")
    #expect(event.t == 1.0)
}

@Test func decodesToolStartWithOptionalFields() throws {
    let json = #"{"type":"ToolStart","call_id":"c1","name":"read","args_preview":"read  foo.py"}"#.data(using: .utf8)!
    let event = try JSONDecoder().decode(Event.self, from: json)
    guard case .toolStart(let s) = event.payload else { Issue.record("wrong case"); return }
    #expect(s.callId == "c1")
    #expect(s.name == "read")
    #expect(s.argsPreview == "read  foo.py")
    #expect(s.subagentId == nil)
    #expect(s.tier == nil)
}

@Test func decodesToolEndWithDefaults() throws {
    let json = #"{"type":"ToolEnd","call_id":"c1","name":"read","result_preview":"...","duration_s":0.4}"#.data(using: .utf8)!
    let event = try JSONDecoder().decode(Event.self, from: json)
    guard case .toolEnd(let e) = event.payload else { Issue.record("wrong case"); return }
    #expect(e.offloaded == false)
    #expect(e.resultChars == 0)
    #expect(e.outcome == "")
    #expect(e.artifactId == nil)
}

@Test func decodesAskUserRequest() throws {
    let json = #"""
    {"type":"ask_user_request","request_id":"r1","question":"Which approach?",
     "options":[{"label":"A","description":"first"},{"label":"B"}],"multi_select":false}
    """#.data(using: .utf8)!
    let event = try JSONDecoder().decode(Event.self, from: json)
    guard case .askUserRequest(let r) = event.payload else { Issue.record("wrong case"); return }
    #expect(r.requestId == "r1")
    #expect(r.options.count == 2)
    #expect(r.options[1].description == nil)
}

@Test func unknownTypeDecodesGracefully() throws {
    let json = #"{"type":"SomethingNew","foo":"bar"}"#.data(using: .utf8)!
    let event = try JSONDecoder().decode(Event.self, from: json)
    guard case .unknown(let type) = event.payload else { Issue.record("wrong case"); return }
    #expect(type == "SomethingNew")
}

@Test func decodesPhaseEnum() throws {
    let json = #"{"type":"Phase","state":"thinking"}"#.data(using: .utf8)!
    let event = try JSONDecoder().decode(Event.self, from: json)
    guard case .phase(let p) = event.payload else { Issue.record("wrong case"); return }
    #expect(p.state == .thinking)
}

@Test func serveConfigDecodesFromDisk() throws {
    let json = #"{"host":"127.0.0.1","port":7777,"token":"abc","pid":42}"#.data(using: .utf8)!
    let config = try JSONDecoder().decode(ServeConfig.self, from: json)
    #expect(config.port == 7777)
    #expect(config.baseURL.absoluteString == "http://127.0.0.1:7777")
}
