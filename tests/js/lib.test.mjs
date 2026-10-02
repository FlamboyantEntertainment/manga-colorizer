import assert from "node:assert/strict";
import { test } from "node:test";

import {
  JobQueue, LruCache, base64ToBytes, bytesToBase64, matchingPreset, normalizeSettings, refererRuleId,
  settingsKey, storedSettings,
} from "../../extension/lib.js";

test("pop returns the job closest to the viewport", () => {
  const queue = new JobQueue();
  queue.add("far", {}, "1:1", 900);
  queue.add("near", {}, "1:2", 10);
  queue.add("mid", {}, "1:3", 300);
  assert.deepEqual([queue.pop().key, queue.pop().key, queue.pop().key], ["near", "mid", "far"]);
  assert.equal(queue.pop(), null);
});

test("ties keep insertion order", () => {
  const queue = new JobQueue();
  queue.add("a", {}, "1:1", 0);
  queue.add("b", {}, "1:2", 0);
  assert.equal(queue.pop().key, "a");
});

test("same key merges waiters and uses the smallest distance", () => {
  const queue = new JobQueue();
  queue.add("page", { url: "x" }, "1:1", 800);
  queue.add("other", {}, "1:2", 100);
  queue.add("page", { url: "x" }, "2:1", 5);
  assert.equal(queue.size, 2);
  const job = queue.pop();
  assert.equal(job.key, "page");
  assert.deepEqual([...job.waiters.keys()], ["1:1", "2:1"]);
});

test("setDistance reorders", () => {
  const queue = new JobQueue();
  queue.add("a", {}, "1:1", 10);
  queue.add("b", {}, "1:2", 20);
  queue.setDistance("1:2", 1);
  assert.equal(queue.pop().key, "b");
});

test("removing the last waiter drops the job", () => {
  const queue = new JobQueue();
  queue.add("a", {}, "1:1", 10);
  queue.add("a", {}, "2:1", 10);
  queue.add("b", {}, "1:2", 20);
  queue.removeWaitersWhere((waiterKey) => waiterKey.startsWith("1:"));
  assert.equal(queue.size, 1);
  assert.deepEqual([...queue.pop().waiters.keys()], ["2:1"]);
});

test("removeJobsWhere drops matching jobs", () => {
  const queue = new JobQueue();
  queue.add("a", { preset: "natural" }, "1:1", 0);
  queue.add("b", { preset: "vivid" }, "1:2", 0);
  queue.removeJobsWhere((job) => job.payload.preset !== "vivid");
  assert.equal(queue.pop().key, "b");
  assert.equal(queue.size, 0);
});

test("requeue puts a popped job back with its waiters", () => {
  const queue = new JobQueue();
  queue.add("a", { url: "x" }, "1:1", 3);
  const job = queue.pop();
  queue.requeue(job);
  assert.equal(queue.size, 1);
  queue.setDistance("1:1", 0);
  assert.deepEqual(queue.pop().payload, { url: "x" });
});

test("requeue of a job whose waiters all left is a no-op", () => {
  const queue = new JobQueue();
  queue.add("a", {}, "1:1", 3);
  const job = queue.pop();
  job.waiters.clear();
  queue.requeue(job);
  assert.equal(queue.size, 0);
});

test("LruCache evicts the least recently used entry", () => {
  const cache = new LruCache(2);
  cache.set("a", 1);
  cache.set("b", 2);
  assert.equal(cache.get("a"), 1); // "a" is now most recent
  cache.set("c", 3);
  assert.equal(cache.get("b"), undefined);
  assert.equal(cache.get("a"), 1);
  assert.equal(cache.get("c"), 3);
  assert.equal(cache.size, 2);
});

test("base64 round-trips large binary data", () => {
  const bytes = new Uint8Array(200_000);
  for (let index = 0; index < bytes.length; index++) bytes[index] = (index * 31) % 256;
  const encoded = bytesToBase64(bytes);
  assert.equal(encoded, Buffer.from(bytes).toString("base64"));
  assert.deepEqual(base64ToBytes(encoded), bytes);
});

test("refererRuleId is stable and in range", () => {
  const id = refererRuleId("cdn.example.com");
  assert.equal(id, refererRuleId("cdn.example.com"));
  assert.notEqual(id, refererRuleId("img.example.com"));
  assert.ok(Number.isInteger(id) && id >= 1 && id <= 2_000_000_000);
});

test("waiterKeys lists every waiter still in the queue", () => {
  const queue = new JobQueue();
  queue.add("a", {}, "1:1", 0);
  queue.add("a", {}, "2:1", 0);
  queue.add("b", {}, "1:2", 5);
  assert.deepEqual([...queue.waiterKeys()].sort(), ["1:1", "1:2", "2:1"]);
});

test("normalizeSettings fills defaults, clamps ranges and drops unknown keys", () => {
  assert.deepEqual(normalizeSettings({ saturation: 9, hue: -500, size: "768", skip_colored: false, junk: 1 }), {
    saturation: 2.5, warmth: 0, hue: -180, clean_whites: 0.5, size: 768, denoise: 25, skip_colored: false,
  });
  assert.deepEqual(normalizeSettings({ warmth: "nope" }).warmth, 0);
  assert.deepEqual(normalizeSettings(undefined).size, 576);
});

test("settingsKey is stable regardless of key order", () => {
  const a = settingsKey({ warmth: 0.2, saturation: 1.5 });
  const b = settingsKey({ saturation: 1.5, warmth: 0.2 });
  assert.equal(a, b);
  assert.notEqual(a, settingsKey({ saturation: 1.5, warmth: 0.25 }));
});

test("storedSettings prefers saved settings and falls back to a legacy preset name", () => {
  assert.equal(storedSettings({ settings: { saturation: 2 }, preset: "soft" }).saturation, 2);
  assert.equal(storedSettings({ preset: "vivid" }).saturation, 1.6);
  assert.equal(storedSettings({}).saturation, 1);
});

test("matchingPreset names the preset whose tone matches, else null", () => {
  assert.equal(matchingPreset(normalizeSettings({ saturation: 0.7, warmth: 0.1, clean_whites: 0.6, size: 1024 })), "soft");
  assert.equal(matchingPreset(normalizeSettings({ saturation: 0.75 })), null);
});
