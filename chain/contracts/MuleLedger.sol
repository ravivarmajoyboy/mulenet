// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title MuleLedger
/// @notice On-chain mirror of the off-chain reference ledger in mulenet/ledger.py.
///
/// Design mirrors the Python version on purpose, so the pitch and the code agree:
///  - No raw identifiers on chain. Off-chain, each bank computes keyHash = HMAC(epochKey, id);
///    the contract only ever sees keyHash. (Production needs an OPRF/PSI service - a shared
///    HMAC key lets any member dictionary-test low-entropy IDs; see README caveats.)
///  - One bank's signal -> SOFT (step-up only). Two or more *distinct* banks on the same key
///    within their TTL windows -> HARD (hold). This bounds the damage a single malicious or
///    compromised bank can do: it can force extra auth on a key, never a unilateral freeze.
///  - Signals expire (TTL), a bank may revoke its own signal, and governance can clear a key
///    entirely (appeal upheld) without erasing the audit history - every state change is a
///    logged event, so the full chain of who-flagged-what-when is reconstructable on-chain,
///    which is the property a real audit or regulator inquiry needs.
///  - Per-bank publish rate limit (simple fixed-window counter) to blunt ledger-flooding.
///
/// What's deliberately NOT here: bank enrolment/governance is owner-controlled for the demo;
/// a production deployment would put membership behind its own governance process, and identity
/// behind bank-controlled signing keys (e.g. one contract role per bank, granted by a
/// multi-sig or a regulator-run registry), not a single deployer-owned admin key.
contract MuleLedger {
    enum Kind { Account, Device }

    struct Signal {
        uint64 expiresAt;
        bool active;
    }

    address public owner;
    address public governance;
    uint256 public hardBankThreshold = 2;
    uint256 public rateLimitPerWindow = 500;
    uint256 public rateLimitWindow = 1 hours;

    mapping(address => bool) public isBank;
    address[] public banks;

    // keyHash => kind => bank => signal
    mapping(bytes32 => mapping(uint8 => mapping(address => Signal))) private signals;
    // keyHash => kind => banks that have ever reported (for iteration; may include expired/revoked)
    mapping(bytes32 => mapping(uint8 => address[])) private reporters;
    mapping(bytes32 => mapping(uint8 => mapping(address => bool))) private everReported;

    // rate limiting: bank => window start => count
    mapping(address => uint256) private windowStart;
    mapping(address => uint256) private windowCount;

    event SignalPublished(address indexed bank, bytes32 indexed keyHash, uint8 kind, uint64 expiresAt);
    event SignalRevoked(address indexed bank, bytes32 indexed keyHash, uint8 kind);
    event AppealUpheld(address indexed governance, bytes32 indexed keyHash, uint8 kind);
    event BankEnrolled(address indexed bank);

    modifier onlyOwner() {
        require(msg.sender == owner, "MuleLedger: not owner");
        _;
    }

    modifier onlyBank() {
        require(isBank[msg.sender], "MuleLedger: not an enrolled bank");
        _;
    }

    modifier onlyGovernance() {
        require(msg.sender == governance, "MuleLedger: not governance");
        _;
    }

    constructor(address _governance) {
        owner = msg.sender;
        governance = _governance;
    }

    // ---- admin -------------------------------------------------------
    function enrollBank(address bank) external onlyOwner {
        require(!isBank[bank], "MuleLedger: already enrolled");
        isBank[bank] = true;
        banks.push(bank);
        emit BankEnrolled(bank);
    }

    function setHardBankThreshold(uint256 n) external onlyOwner {
        require(n >= 1, "MuleLedger: threshold must be >=1");
        hardBankThreshold = n;
    }

    function setRateLimit(uint256 perWindow, uint256 windowSeconds) external onlyOwner {
        rateLimitPerWindow = perWindow;
        rateLimitWindow = windowSeconds;
    }

    // ---- bank actions --------------------------------------------------
    function publish(bytes32 keyHash, Kind kind, uint64 ttlSeconds) external onlyBank {
        _checkRateLimit(msg.sender);
        uint8 k = uint8(kind);
        if (!everReported[keyHash][k][msg.sender]) {
            everReported[keyHash][k][msg.sender] = true;
            reporters[keyHash][k].push(msg.sender);
        }
        uint64 expiresAt = uint64(block.timestamp) + ttlSeconds;
        signals[keyHash][k][msg.sender] = Signal({ expiresAt: expiresAt, active: true });
        emit SignalPublished(msg.sender, keyHash, k, expiresAt);
    }

    function revoke(bytes32 keyHash, Kind kind) external onlyBank {
        uint8 k = uint8(kind);
        signals[keyHash][k][msg.sender].active = false;
        emit SignalRevoked(msg.sender, keyHash, k);
    }

    // ---- governance ------------------------------------------------
    /// @notice Appeal upheld: clears every bank's active signal on this key. History (events)
    /// is untouched - the appeal itself is logged, not a deletion of what happened.
    function upholdAppeal(bytes32 keyHash, Kind kind) external onlyGovernance {
        uint8 k = uint8(kind);
        address[] storage rs = reporters[keyHash][k];
        for (uint256 i = 0; i < rs.length; i++) {
            signals[keyHash][k][rs[i]].active = false;
        }
        emit AppealUpheld(msg.sender, keyHash, k);
    }

    function setGovernance(address g) external onlyOwner {
        governance = g;
    }

    // ---- views -----------------------------------------------------
    /// @return tier 0 = none, 1 = soft (one bank), 2 = hard (>= hardBankThreshold distinct banks)
    /// @return activeCount number of distinct banks with a currently-active, unexpired signal
    function status(bytes32 keyHash, Kind kind) public view returns (uint8 tier, uint256 activeCount) {
        uint8 k = uint8(kind);
        address[] storage rs = reporters[keyHash][k];
        for (uint256 i = 0; i < rs.length; i++) {
            Signal storage s = signals[keyHash][k][rs[i]];
            if (s.active && s.expiresAt > block.timestamp) {
                activeCount++;
            }
        }
        if (activeCount == 0) return (0, 0);
        tier = activeCount >= hardBankThreshold ? 2 : 1;
    }

    function bankCount() external view returns (uint256) {
        return banks.length;
    }

    // ---- internal ----------------------------------------------------
    function _checkRateLimit(address bank) internal {
        uint256 w = block.timestamp / rateLimitWindow;
        if (windowStart[bank] != w) {
            windowStart[bank] = w;
            windowCount[bank] = 0;
        }
        windowCount[bank] += 1;
        require(windowCount[bank] <= rateLimitPerWindow, "MuleLedger: rate limited");
    }
}
