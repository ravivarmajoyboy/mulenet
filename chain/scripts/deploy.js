// Usage:
//   npx hardhat run scripts/deploy.js                 # local in-memory Hardhat network (default)
//   npx hardhat run scripts/deploy.js --network sepolia   # real testnet, once configured below
//
// A real testnet deploy needs a network entry in hardhat.config.js with a funded wallet, e.g.:
//   sepolia: { url: process.env.SEPOLIA_RPC_URL, accounts: [process.env.DEPLOYER_KEY] }
// This sandbox has no outbound access to any RPC endpoint, so that step has to run on your
// machine or CI, not here - the contract and its tests are fully verified locally either way.
const hre = require("hardhat");

async function main() {
  const [deployer, governance, bankA, bankB, bankC] = await hre.ethers.getSigners();
  console.log("deployer:", deployer.address);

  const Ledger = await hre.ethers.getContractFactory("MuleLedger");
  const ledger = await Ledger.deploy(governance.address);
  await ledger.waitForDeployment();
  console.log("MuleLedger deployed to:", await ledger.getAddress());
  console.log("governance:", governance.address);

  for (const b of [bankA, bankB, bankC]) {
    await (await ledger.enrollBank(b.address)).wait();
    console.log("enrolled bank:", b.address);
  }
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
