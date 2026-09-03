import Testing
@testable import OmegaCore

@Test func versionIsSet() {
    #expect(!OmegaCore.version.isEmpty)
}
