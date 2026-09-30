// a-48: recompute the Board ledger's hash chain in JavaScript, from the CSV,
// using nothing but the contract's x-contract.tables.board_ledger.hash_chain spec.
// Proves a browser (String(n), JSON.stringify, SHA-256) reproduces the Python
// row_hash on every row - so the site can verify the chain, not just print it.
//
//   node research/a48_chain_parity.mjs <tree-with-board/nfl/ledger.csv> [contract.json]
//
// Exit 1 on any mismatch, 2 on a CSV this naive parser cannot read (quotes).
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { join } from "node:path";

const tree = process.argv[2];
const contractPath = process.argv[3] ?? new URL("../web/contract/v2/contract.schema.json", import.meta.url);
const t = JSON.parse(readFileSync(contractPath, "utf8"))["x-contract"].tables.board_ledger;
const hc = t.hash_chain;
const text = readFileSync(join(tree, "board", "nfl", "ledger.csv"), "utf8");
if (text.includes('"')) { console.log("CSV has quoted fields - this parser splits on commas only"); process.exit(2); }
const lines = text.trim().split(/\r?\n/);
const head = lines[0].split(",");
let prev = hc.genesis, ok = 0, bad = 0, first = null;
lines.slice(1).forEach((line, i) => {
  const f = line.split(",");
  const r = Object.fromEntries(head.map((c, j) => [c, f[j]]));
  const cells = hc.covers.map((c) => {
    const v = r[c];
    if (v === "") return null;
    const ty = t.columns[c];
    return ty === "string" ? v : ty === "int64" ? String(BigInt(v)) : String(Number(v));
  });
  const h = createHash("sha256").update(JSON.stringify([hc.tag, ...cells, prev]), "utf8").digest("hex");
  if (h === r[hc.row] && prev === r[hc.prev]) ok++; else { bad++; first ??= i; }
  prev = h;
});
if (lines.length - 1 === 0) { console.log("no rows - nothing checked"); process.exit(1); }
console.log(`node: ${ok} of ${lines.length - 1} rows reproduce row_hash` +
  (bad ? `, ${bad} do not (first at row ${first})` : "") + `; head ${prev}`);
process.exit(bad ? 1 : 0);
