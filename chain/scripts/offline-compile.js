// Hardhat's default `compile` task downloads the solc binary from
// binaries.soliditylang.org, which is blocked in some sandboxed/offline CI environments.
// This compiles with the solc npm package instead (fully local) and writes artifacts in
// Hardhat's exact artifact format, so `npx hardhat test --no-compile` picks them up normally.
// If your environment CAN reach binaries.soliditylang.org, you don't need this - just run
// `npx hardhat compile && npx hardhat test`.
const fs = require("fs");
const path = require("path");
const solc = require("solc");

const CONTRACTS_DIR = path.join(__dirname, "..", "contracts");
const ARTIFACTS_DIR = path.join(__dirname, "..", "artifacts", "contracts");

function compileFile(fileName) {
  const source = fs.readFileSync(path.join(CONTRACTS_DIR, fileName), "utf8");
  const input = {
    language: "Solidity",
    sources: { [fileName]: { content: source } },
    settings: {
      optimizer: { enabled: true, runs: 200 },
      outputSelection: { "*": { "*": ["abi", "evm.bytecode.object", "evm.deployedBytecode.object"] } },
    },
  };

  const output = JSON.parse(solc.compile(JSON.stringify(input)));
  const errors = (output.errors || []).filter((e) => e.severity === "error");
  if (errors.length) {
    errors.forEach((e) => console.error(e.formattedMessage));
    throw new Error(`solc compilation failed for ${fileName}`);
  }
  (output.errors || []).forEach((e) => console.warn(e.formattedMessage));

  for (const [contractName, c] of Object.entries(output.contracts[fileName])) {
    const outDir = path.join(ARTIFACTS_DIR, fileName);
    fs.mkdirSync(outDir, { recursive: true });
    const artifact = {
      _format: "hh-sol-artifact-1",
      contractName,
      sourceName: `contracts/${fileName}`,
      abi: c.abi,
      bytecode: "0x" + c.evm.bytecode.object,
      deployedBytecode: "0x" + c.evm.deployedBytecode.object,
      linkReferences: {},
      deployedLinkReferences: {},
    };
    fs.writeFileSync(path.join(outDir, `${contractName}.json`), JSON.stringify(artifact, null, 2));
    console.log(`wrote ${outDir}/${contractName}.json`);
  }
}

fs.readdirSync(CONTRACTS_DIR)
  .filter((f) => f.endsWith(".sol"))
  .forEach(compileFile);
