#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const MAX_MAP_FILES = 2_000;
const MAX_DISCOVERED_FILES = 5_000;
const MAX_MAP_BYTES = 128 * 1024 * 1024;
const MAX_TOTAL_BYTES = 512 * 1024 * 1024;
// The vulnerable code is in these libraries. Expo packages are dependency
// parents/tooling and can contribute unrelated safe modules to runtime bundles.
const AFFECTED_PACKAGES = ["glob", "minimatch"];

function fail(message) {
  throw new Error(message);
}

function walk(directory, result = { maps: [], bundles: [], entries: 0 }) {
  let entries;
  try {
    entries = fs.readdirSync(directory, { withFileTypes: true });
  } catch (error) {
    fail(`Cannot read export directory ${directory}: ${error.message}`);
  }
  for (const entry of entries) {
    result.entries += 1;
    if (result.entries > MAX_DISCOVERED_FILES) fail(`Export contains more than ${MAX_DISCOVERED_FILES} filesystem entries; refusing unbounded scan.`);
    const fullPath = path.join(directory, entry.name);
    if (entry.isSymbolicLink()) fail(`Refusing symbolic link in export: ${fullPath}`);
    if (entry.isDirectory()) walk(fullPath, result);
    else if (entry.isFile()) {
      if (entry.name.endsWith(".map")) result.maps.push(fullPath);
      else if (/\.(?:js|hbc)$/i.test(entry.name)) result.bundles.push(fullPath);
    }
  }
  return result;
}

function sourcePackage(source, sourceRoot = "") {
  if (typeof source !== "string") return null;
  const normalized = `${sourceRoot}/${source}`.replaceAll("\\", "/").split(/[?#]/, 1)[0];
  const parts = normalized.split("/").filter(Boolean);
  for (let index = 0; index < parts.length; index += 1) {
    if (parts[index] !== "node_modules") continue;
    const next = parts[index + 1];
    if (!next) return null;
    if (next.startsWith("@")) {
      const scopedName = parts[index + 2] ? `${next}/${parts[index + 2]}` : null;
      if (AFFECTED_PACKAGES.includes(scopedName)) return scopedName;
    } else if (AFFECTED_PACKAGES.includes(next)) {
      return next;
    }
  }
  return null;
}

function collectSources(map, file, found) {
  if (!map || typeof map !== "object") fail(`Invalid source map object: ${file}`);
  if (Array.isArray(map.sections)) {
    if (map.sections.length === 0) fail(`Empty indexed source map: ${file}`);
    let total = 0;
    for (const section of map.sections) {
      if (section?.url) fail(`External source-map section URL is not auditable: ${file}`);
      total += collectSources(section?.map, file, found);
    }
    return total;
  }
  if (!Array.isArray(map.sources) || map.sources.length === 0) {
    fail(`Source map has no source list: ${file}`);
  }
  for (const source of map.sources) {
    const packageName = sourcePackage(source, map.sourceRoot || "");
    if (packageName) found.set(packageName, (found.get(packageName) || 0) + 1);
  }
  return map.sources.length;
}

/** Inspect Metro source-map module paths; missing, incomplete, or oversized input fails closed. */
export function inspectBundleSourceMaps(exportDirectory) {
  if (typeof exportDirectory !== "string" || !exportDirectory.trim()) {
    fail("Pass the path to a fresh Expo export directory containing external source maps.");
  }
  const root = path.resolve(exportDirectory);
  if (!fs.existsSync(root) || !fs.statSync(root).isDirectory()) {
    fail(`Export directory does not exist: ${root}`);
  }
  const { maps, bundles } = walk(root);
  if (maps.length === 0) fail("No source maps found; rerun Expo export with --source-maps external.");
  if (bundles.length === 0) fail("No production JavaScript or Hermes bundles found in export directory.");
  if (maps.length > MAX_MAP_FILES) fail(`Found more than ${MAX_MAP_FILES} source maps; refusing unbounded scan.`);
  const mapPaths = new Set(maps.map((file) => path.resolve(file)));
  for (const bundle of bundles) {
    if (!mapPaths.has(path.resolve(`${bundle}.map`))) {
      fail(`Bundle has no matching external source map: ${bundle}`);
    }
  }

  const found = new Map();
  let totalBytes = 0;
  let totalSources = 0;
  for (const file of maps) {
    const size = fs.statSync(file).size;
    if (size > MAX_MAP_BYTES) fail(`Source map exceeds the ${MAX_MAP_BYTES} byte scan limit: ${file}`);
    totalBytes += size;
    if (totalBytes > MAX_TOTAL_BYTES) fail(`Source maps exceed the ${MAX_TOTAL_BYTES} byte total scan limit.`);
    let map;
    try {
      map = JSON.parse(fs.readFileSync(file, "utf8"));
    } catch (error) {
      fail(`Cannot parse source map ${file}: ${error.message}`);
    }
    const sourcesInMap = collectSources(map, file, found);
    if (sourcesInMap <= 0) fail(`Source map has no auditable module entries: ${file}`);
    totalSources += sourcesInMap;
  }

  const packages = [...found.keys()].sort();
  return { ok: packages.length === 0, mapCount: maps.length, totalSources, packages, packageSourceCounts: Object.fromEntries([...found].sort()) };
}

function main() {
  try {
    const exportDirectory = process.argv[2];
    if (process.argv.length !== 3) fail("Usage: node scripts/audit-mobile-bundle-content.mjs <fresh-export-directory>");
    const result = inspectBundleSourceMaps(exportDirectory);
    console.log(`Scanned ${result.totalSources} Metro source entries across ${result.mapCount} source maps.`);
    if (!result.ok) {
      console.error(`BLOCKED: affected package source found in production bundle: ${result.packages.join(", ")}.`);
      for (const [name, count] of Object.entries(result.packageSourceCounts)) console.error(`- ${name}: ${count} source entries`);
      process.exitCode = 1;
      return;
    }
    console.log("PASS: no source paths for the temporarily allowlisted Expo/glob/minimatch packages were found.");
  } catch (error) {
    console.error(`BLOCKED: ${error.message}`);
    process.exitCode = 1;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
