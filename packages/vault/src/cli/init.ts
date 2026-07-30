import { loadConfig } from "@my-brain/config";
import { ensureVaultSkeleton } from "../skeleton.js";

const config = loadConfig({ requireVaultExists: false });
const dirs = ensureVaultSkeleton(config.vaultPath);
console.log(`Vault skeleton ready at ${config.vaultPath}`);
for (const d of dirs) console.log(`  - ${d}/`);
