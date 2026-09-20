import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
const archiveUrl = process.env.RUNTIME_ARTIFACTS_URL;
const expectedFiles = [
  "ml/models/demand_mean.onnx",
  "ml/models/demand_p10.onnx",
  "ml/models/demand_p90.onnx",
  "ml/models/feature_columns.json",
  "ml/models/zone_codes.json",
  "ml/models/region_polygons.geojson",
  "ml/data/pickups_15m.parquet",
  "data/noaa_isd_nyc/72502014734_2019.csv",
  "data/noaa_isd_nyc/72409454743_2019.csv",
  "data/noaa_isd_nyc/72058100178_2019.csv",
  "data/noaa_isd_nyc/72055399999_2019.csv",
  "data/noaa_isd_nyc/72502594741_2019.csv",
  "data/noaa_isd_nyc/72505394728_2019.csv",
  "data/noaa_isd_nyc/72503014732_2019.csv",
  "data/noaa_isd_nyc/74486094789_2019.csv",
  "data/noaa_isd_nyc/99727199999_2019.csv",
  "data/noaa_isd_nyc/99728099999_2019.csv",
  "data/noaa_isd_nyc/99727299999_2019.csv",
  "data/noaa_isd_nyc/99728999999_2019.csv",
  "data/noaa_isd_nyc/99774399999_2019.csv",
  "data/gdelt_nyc/events/gdelt_events_nyc_2019_10.parquet",
];

if (expectedFiles.every((file) => existsSync(resolve(root, file)))) {
  console.log("Runtime artifacts already exist; skipping download.");
  process.exit(0);
}

if (!archiveUrl) {
  console.error("RUNTIME_ARTIFACTS_URL is required when runtime artifacts are missing.");
  process.exit(1);
}

async function download(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Download failed with HTTP ${response.status}: ${url}`);
  return Buffer.from(await response.arrayBuffer());
}

const archive = await download(archiveUrl);
const archivePath = resolve(tmpdir(), `nyc-rat-race-runtime-${process.pid}.zip`);
writeFileSync(archivePath, archive);

let expectedHash = process.env.RUNTIME_ARTIFACTS_SHA256?.trim().toLowerCase();
if (!expectedHash) {
  const checksumUrl = `${archiveUrl}.sha256`;
  try {
    expectedHash = (await download(checksumUrl)).toString("utf8").trim().split(/\s+/)[0].toLowerCase();
  } catch {
    console.error(`Could not download ${checksumUrl}. Set RUNTIME_ARTIFACTS_SHA256 explicitly.`);
    rmSync(archivePath, { force: true });
    process.exit(1);
  }
}

const actualHash = createHash("sha256").update(archive).digest("hex");
if (actualHash !== expectedHash) {
  rmSync(archivePath, { force: true });
  throw new Error(`Runtime artifact checksum mismatch: expected ${expectedHash}, got ${actualHash}`);
}

const python = process.platform === "win32" ? "py" : "python3";
const extractScript = `
import pathlib
import sys
import zipfile

root = pathlib.Path(sys.argv[1]).resolve()
archive_path = pathlib.Path(sys.argv[2]).resolve()
with zipfile.ZipFile(archive_path) as archive:
    for member in archive.infolist():
        target = (root / member.filename).resolve()
        if target != root and root not in target.parents:
            raise RuntimeError(f"Unsafe archive member: {member.filename}")
    archive.extractall(root)
`;
const result = spawnSync(python, ["-c", extractScript, root, archivePath], { stdio: "inherit" });
rmSync(archivePath, { force: true });
if (result.status !== 0) process.exit(result.status ?? 1);

const missing = expectedFiles.filter((file) => !existsSync(resolve(root, file)));
if (missing.length > 0) {
  console.error("Runtime archive did not contain:");
  for (const file of missing) console.error(`  ${file}`);
  process.exit(1);
}
console.log(`Installed runtime artifacts from ${archiveUrl}`);
