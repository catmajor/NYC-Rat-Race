import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
const output = resolve(root, process.env.RUNTIME_BUNDLE_OUTPUT ?? "runtime-artifacts.zip");
const files = [
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

const missing = files.filter((file) => !existsSync(resolve(root, file)));
if (missing.length > 0) {
  console.error("Missing runtime artifacts:");
  for (const file of missing) console.error(`  ${file}`);
  console.error("Generate/download the ML and scenario data before packaging.");
  process.exit(1);
}

mkdirSync(dirname(output), { recursive: true });
if (existsSync(output)) rmSync(output);

const python = process.platform === "win32" ? "py" : "python3";
const archiveScript = `
import pathlib
import sys
import zipfile

root = pathlib.Path(sys.argv[1]).resolve()
output = pathlib.Path(sys.argv[2]).resolve()
files = sys.argv[3:]
with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for relative in files:
        path = root / relative
        archive.write(path, relative)
`;
const result = spawnSync(
  python,
  ["-c", archiveScript, root, output, ...files],
  { stdio: "inherit" },
);
if (result.status !== 0) process.exit(result.status ?? 1);

const digest = createHash("sha256").update(readFileSync(output)).digest("hex");
writeFileSync(`${output}.sha256`, `${digest}  ${output.split(/[\\/]/).pop()}\n`);
console.log(`Created ${output}`);
console.log(`SHA-256 ${digest}`);
