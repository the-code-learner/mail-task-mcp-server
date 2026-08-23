from __future__ import annotations

import json
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "extras/cloudflare-email-privacy-proxy/src/index.js"


class WorkerSecretStateNodeIntegrationV966Tests(unittest.TestCase):
    def test_worker_generation_order_grace_idempotency_and_deprovision(self):
        script = textwrap.dedent(
            r"""
            import fs from "node:fs";
            const workerPath = process.argv[2];
            const source = fs.readFileSync(workerPath, "utf8");
            const moduleUrl = "data:text/javascript;base64," + Buffer.from(source).toString("base64");
            const worker = await import(moduleUrl);

            const values = new Map();
            const state = {
              storage: {
                sql: { exec(_query, ..._bindings) { return []; } },
                async get(key) { return values.get(key); },
                async put(key, value) { values.set(key, value); },
                async delete(key) { return values.delete(key); },
              },
            };
            const guard = new worker.NonceGuard(state);

            async function apply(payload) {
              const response = await guard.fetch(new Request("https://nonce-guard/secret-state", {
                method: "POST",
                headers: { "content-type": "application/json" },
                body: JSON.stringify(payload),
              }));
              let body = {};
              try { body = await response.json(); } catch (_) { /* status-only failure */ }
              return { status: response.status, body };
            }

            async function readState() {
              const response = await guard.fetch(new Request("https://nonce-guard/secret-state"));
              return response.json();
            }

            const secret1 = "generation-one-secret-material-abcdefghijklmnopqrstuvwxyz";
            const secret2 = "generation-two-secret-material-abcdefghijklmnopqrstuvwxyz";
            const conflict = "conflicting-secret-material-abcdefghijklmnopqrstuvwxyz-00";

            const provision = await apply({
              generation: 1,
              operation: "provision",
              secret: secret1,
              previous_secret_grace_seconds: 120,
            });
            const skipped = await apply({
              generation: 3,
              operation: "rotate",
              secret: secret2,
              previous_secret_grace_seconds: 120,
            });
            const beforeRotate = Math.floor(Date.now() / 1000);
            const rotate = await apply({
              generation: 2,
              operation: "rotate",
              secret: secret2,
              previous_secret_grace_seconds: 999,
            });
            const afterRotate = Math.floor(Date.now() / 1000);
            const rotatedState = await readState();
            const rollback = await apply({
              generation: 1,
              operation: "rotate",
              secret: secret1,
              previous_secret_grace_seconds: 120,
            });
            const generationConflict = await apply({
              generation: 2,
              operation: "rotate",
              secret: conflict,
              previous_secret_grace_seconds: 120,
            });
            const idempotent = await apply({
              generation: 2,
              operation: "rotate",
              secret: secret2,
              previous_secret_grace_seconds: 120,
            });
            const deprovision = await apply({ generation: 3, operation: "deprovision" });
            const finalState = await readState();

            console.log(JSON.stringify({
              provisionStatus: provision.status,
              skippedStatus: skipped.status,
              skippedError: skipped.body.error,
              rotateStatus: rotate.status,
              rotatedGeneration: rotatedState.generation,
              rotatedActiveIsNew: rotatedState.active_secret === secret2,
              rotatedPreviousIsOld: rotatedState.previous_secret === secret1,
              graceNotBeforeNow: Number(rotatedState.previous_valid_until) >= beforeRotate,
              graceBounded: Number(rotatedState.previous_valid_until) <= afterRotate + 300,
              rollbackStatus: rollback.status,
              rollbackError: rollback.body.error,
              conflictStatus: generationConflict.status,
              conflictError: generationConflict.body.error,
              idempotentStatus: idempotent.status,
              idempotentFlag: idempotent.body.idempotent === true,
              deprovisionStatus: deprovision.status,
              finalGeneration: finalState.generation,
              finalActiveEmpty: !finalState.active_secret,
              finalPreviousEmpty: !finalState.previous_secret,
            }));
            """
        )
        with tempfile.TemporaryDirectory() as tmp:
            script_path = Path(tmp) / "worker-state.mjs"
            script_path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                ["node", str(script_path), str(WORKER)],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
                timeout=30,
            )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"node Worker state integration failed\nstdout={completed.stdout}\nstderr={completed.stderr}",
        )
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(result["provisionStatus"], 200)
        self.assertEqual(result["skippedStatus"], 409)
        self.assertEqual(result["skippedError"], "generation_out_of_order")
        self.assertEqual(result["rotateStatus"], 200)
        self.assertEqual(result["rotatedGeneration"], 2)
        self.assertTrue(result["rotatedActiveIsNew"])
        self.assertTrue(result["rotatedPreviousIsOld"])
        self.assertTrue(result["graceNotBeforeNow"])
        self.assertTrue(result["graceBounded"])
        self.assertEqual(result["rollbackStatus"], 409)
        self.assertEqual(result["rollbackError"], "generation_out_of_order")
        self.assertEqual(result["conflictStatus"], 409)
        self.assertEqual(result["conflictError"], "generation_conflict")
        self.assertEqual(result["idempotentStatus"], 200)
        self.assertTrue(result["idempotentFlag"])
        self.assertEqual(result["deprovisionStatus"], 200)
        self.assertEqual(result["finalGeneration"], 3)
        self.assertTrue(result["finalActiveEmpty"])
        self.assertTrue(result["finalPreviousEmpty"])


if __name__ == "__main__":
    unittest.main()
