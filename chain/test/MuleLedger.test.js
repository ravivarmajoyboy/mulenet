const { expect } = require("chai");
const { ethers } = require("hardhat");
const { time } = require("@nomicfoundation/hardhat-toolbox/network-helpers");
const { anyValue } = require("@nomicfoundation/hardhat-chai-matchers/withArgs");

const Kind = { Account: 0, Device: 1 };
const keyHash = (raw) => ethers.keccak256(ethers.toUtf8Bytes(raw)); // stand-in for the off-chain HMAC blind

describe("MuleLedger", function () {
  async function deploy() {
    const [owner, gov, bankA, bankB, bankC, other] = await ethers.getSigners();
    const Ledger = await ethers.getContractFactory("MuleLedger");
    const ledger = await Ledger.deploy(gov.address);
    for (const b of [bankA, bankB, bankC]) await ledger.connect(owner).enrollBank(b.address);
    return { ledger, owner, gov, bankA, bankB, bankC, other };
  }

  it("one bank publishing gives SOFT, a second distinct bank gives HARD", async function () {
    const { ledger, bankA, bankB } = await deploy();
    const k = keyHash("acct-1");
    await ledger.connect(bankA).publish(k, Kind.Account, 7 * 86400);
    let [tier, n] = await ledger.status(k, Kind.Account);
    expect(tier).to.equal(1);
    expect(n).to.equal(1);

    await ledger.connect(bankB).publish(k, Kind.Account, 7 * 86400);
    [tier, n] = await ledger.status(k, Kind.Account);
    expect(tier).to.equal(2);
    expect(n).to.equal(2);
  });

  it("the same bank repeating its own signal does not corroborate", async function () {
    const { ledger, bankA } = await deploy();
    const k = keyHash("acct-x");
    await ledger.connect(bankA).publish(k, Kind.Account, 1000);
    await ledger.connect(bankA).publish(k, Kind.Account, 1000);
    const [tier, n] = await ledger.status(k, Kind.Account);
    expect(tier).to.equal(1);
    expect(n).to.equal(1);
  });

  it("signals expire (TTL) and a bank can revoke its own", async function () {
    const { ledger, bankA, bankB } = await deploy();
    const k = keyHash("acct-ttl");
    await ledger.connect(bankA).publish(k, Kind.Account, 100);
    expect((await ledger.status(k, Kind.Account))[0]).to.equal(1);
    await time.increase(150);
    expect((await ledger.status(k, Kind.Account))[0]).to.equal(0);

    const k2 = keyHash("acct-revoke");
    await ledger.connect(bankB).publish(k2, Kind.Account, 10000);
    await ledger.connect(bankB).revoke(k2, Kind.Account);
    expect((await ledger.status(k2, Kind.Account))[0]).to.equal(0);
  });

  it("governance can uphold an appeal, clearing all active signals on a key", async function () {
    const { ledger, gov, bankA, bankB, other } = await deploy();
    const k = keyHash("acct-appeal");
    await ledger.connect(bankA).publish(k, Kind.Account, 10000);
    await ledger.connect(bankB).publish(k, Kind.Account, 10000);
    expect((await ledger.status(k, Kind.Account))[0]).to.equal(2);

    await expect(ledger.connect(other).upholdAppeal(k, Kind.Account)).to.be.revertedWith(
      "MuleLedger: not governance"
    );
    await ledger.connect(gov).upholdAppeal(k, Kind.Account);
    expect((await ledger.status(k, Kind.Account))[0]).to.equal(0);

    // a fresh signal afterwards is unaffected by the historical appeal
    await ledger.connect(bankA).publish(k, Kind.Account, 10000);
    expect((await ledger.status(k, Kind.Account))[0]).to.equal(1);
  });

  it("rejects publishes from non-enrolled addresses", async function () {
    const { ledger, other } = await deploy();
    await expect(
      ledger.connect(other).publish(keyHash("x"), Kind.Account, 100)
    ).to.be.revertedWith("MuleLedger: not an enrolled bank");
  });

  it("enforces the per-bank rate limit within a window", async function () {
    const { ledger, owner, bankA } = await deploy();
    await ledger.connect(owner).setRateLimit(3, 3600);
    for (let i = 0; i < 3; i++) {
      await ledger.connect(bankA).publish(keyHash(`id-${i}`), Kind.Account, 100);
    }
    await expect(
      ledger.connect(bankA).publish(keyHash("id-9"), Kind.Account, 100)
    ).to.be.revertedWith("MuleLedger: rate limited");
    await time.increase(3601);
    await ledger.connect(bankA).publish(keyHash("id-9"), Kind.Account, 100); // window rolled over
  });

  it("keeps account and device signals on the same identifier independent", async function () {
    const { ledger, bankA } = await deploy();
    const k = keyHash("shared-device-id");
    await ledger.connect(bankA).publish(k, Kind.Device, 1000);
    expect((await ledger.status(k, Kind.Account))[0]).to.equal(0);
    expect((await ledger.status(k, Kind.Device))[0]).to.equal(1);
  });

  it("emits events for the off-chain indexer / audit trail", async function () {
    const { ledger, bankA } = await deploy();
    const k = keyHash("acct-evt");
    await expect(ledger.connect(bankA).publish(k, Kind.Account, 100))
      .to.emit(ledger, "SignalPublished")
      .withArgs(bankA.address, k, Kind.Account, anyValue);
  });
});
