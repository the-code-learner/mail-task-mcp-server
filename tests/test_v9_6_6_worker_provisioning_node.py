from __future__ import annotations

import json
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "extras/cloudflare-email-privacy-proxy/src/index.js"


class WorkerProvisioningNodeIntegrationV966Tests(unittest.TestCase):
    def test_ed25519_worker_verifier_fails_closed_for_replay_expiry_origin_key_and_generation(self):
        script = textwrap.dedent(
            r"""
            import fs from "node:fs";
            import { webcrypto } from "node:crypto";
            if (!globalThis.crypto) globalThis.crypto = webcrypto;

            const workerPath = process.argv[2];
            const source = fs.readFileSync(workerPath, "utf8");
            const moduleUrl = "data:text/javascript;base64," + Buffer.from(source).toString("base64");
            const worker = await import(moduleUrl);

            const encoder = new TextEncoder();
            const pair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
            const publicRaw = new Uint8Array(await crypto.subtle.exportKey("raw", pair.publicKey));
            const publicKey = Buffer.from(publicRaw).toString("base64url");
            const publicDigest = Buffer.from(await crypto.subtle.digest("SHA-256", publicRaw)).toString("hex");
            const keyId = `pm-ed25519-${publicDigest.slice(0, 16)}`;

            const seen = new Set();
            const env = {
              POSTMASTER_PROVISIONING_PUBLIC_KEY: publicKey,
              POSTMASTER_PROVISIONING_KEY_ID: keyId,
              NONCE_GUARD: {
                idFromName(name) { return name; },
                get(_) {
                  return {
                    async fetch(_url, options) {
                      const value = JSON.parse(options.body);
                      const replayKey = `${value.scope}:${value.nonce}`;
                      if (seen.has(replayKey)) return new Response(null, { status: 409 });
                      seen.add(replayKey);
                      return new Response(null, { status: 204 });
                    },
                  };
                },
              },
            };

            async function sha256Hex(bytes) {
              return Buffer.from(await crypto.subtle.digest("SHA-256", bytes)).toString("hex");
            }

            let counter = 0;
            async function signedCase({
              origin = "https://worker.example",
              requestOrigin = origin,
              timestamp = String(Math.floor(Date.now() / 1000)),
              headerKeyId = keyId,
              generation = 1,
              payloadGeneration = generation,
              operation = "provision",
              mutateSignature = false,
            } = {}) {
              counter += 1;
              const nonce = `nonce-${counter.toString().padStart(4, "0")}-0123456789abcdef`;
              const payload = {
                generation: payloadGeneration,
                operation,
                secret: "server-to-server-only-secret-material-0123456789",
                previous_secret_grace_seconds: 120,
              };
              const bodyText = JSON.stringify(payload, Object.keys(payload).sort());
              const bodyBytes = encoder.encode(bodyText);
              const digest = await sha256Hex(bodyBytes);
              const canonical = worker.provisioningCanonical({
                method: "POST",
                path: "/provision",
                origin,
                timestamp,
                nonce,
                bodyDigest: digest,
                generation,
                operation,
                keyId: headerKeyId,
              });
              let signature = Buffer.from(
                await crypto.subtle.sign("Ed25519", pair.privateKey, encoder.encode(canonical)),
              ).toString("base64url");
              if (mutateSignature) {
                const replacement = signature.startsWith("A") ? "B" : "A";
                signature = replacement + signature.slice(1);
              }
              const headers = {
                "content-type": "application/json",
                "x-postmaster-provisioning-timestamp": timestamp,
                "x-postmaster-provisioning-nonce": nonce,
                "x-postmaster-provisioning-key-id": headerKeyId,
                "x-postmaster-provisioning-generation": String(generation),
                "x-postmaster-provisioning-operation": operation,
                "x-postmaster-provisioning-body-sha256": digest,
                "x-postmaster-provisioning-signature": signature,
              };
              const request = new Request(`${requestOrigin}/provision`, {
                method: "POST",
                headers,
                body: bodyText,
              });
              return { request, bodyBytes, payload, headers };
            }

            const valid = await signedCase();
            const validAccepted = await worker.verifyProvisioningRequest(
              valid.request, env, valid.bodyBytes, valid.payload,
            );
            const replayRequest = new Request(valid.request.url, {
              method: "POST",
              headers: valid.headers,
              body: new TextDecoder().decode(valid.bodyBytes),
            });
            const replayAccepted = await worker.verifyProvisioningRequest(
              replayRequest, env, valid.bodyBytes, valid.payload,
            );

            const badSignature = await signedCase({ mutateSignature: true });
            const badSignatureAccepted = await worker.verifyProvisioningRequest(
              badSignature.request, env, badSignature.bodyBytes, badSignature.payload,
            );

            const expired = await signedCase({
              timestamp: String(Math.floor(Date.now() / 1000) - 301),
            });
            const expiredAccepted = await worker.verifyProvisioningRequest(
              expired.request, env, expired.bodyBytes, expired.payload,
            );

            const wrongOrigin = await signedCase({
              origin: "https://worker.example",
              requestOrigin: "https://other.example",
            });
            const wrongOriginAccepted = await worker.verifyProvisioningRequest(
              wrongOrigin.request, env, wrongOrigin.bodyBytes, wrongOrigin.payload,
            );

            const wrongKey = await signedCase({ headerKeyId: "pm-ed25519-wrong-key" });
            const wrongKeyAccepted = await worker.verifyProvisioningRequest(
              wrongKey.request, env, wrongKey.bodyBytes, wrongKey.payload,
            );

            const wrongGeneration = await signedCase({ generation: 2, payloadGeneration: 1 });
            const wrongGenerationAccepted = await worker.verifyProvisioningRequest(
              wrongGeneration.request, env, wrongGeneration.bodyBytes, wrongGeneration.payload,
            );

            let missingKeyFailsClosed = false;
            try {
              const missing = await signedCase();
              await worker.verifyProvisioningRequest(
                missing.request,
                { ...env, POSTMASTER_PROVISIONING_PUBLIC_KEY: "" },
                missing.bodyBytes,
                missing.payload,
              );
            } catch (error) {
              missingKeyFailsClosed = String(error?.message || error) === "provisioning_key_not_configured";
            }

            console.log(JSON.stringify({
              validAccepted,
              replayAccepted,
              badSignatureAccepted,
              expiredAccepted,
              wrongOriginAccepted,
              wrongKeyAccepted,
              wrongGenerationAccepted,
              missingKeyFailsClosed,
            }));
            """
        )
        with tempfile.TemporaryDirectory() as tmp:
            script_path = Path(tmp) / "verify-worker.mjs"
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
            msg=f"node worker integration failed\nstdout={completed.stdout}\nstderr={completed.stderr}",
        )
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertTrue(result["validAccepted"])
        self.assertFalse(result["replayAccepted"])
        self.assertFalse(result["badSignatureAccepted"])
        self.assertFalse(result["expiredAccepted"])
        self.assertFalse(result["wrongOriginAccepted"])
        self.assertFalse(result["wrongKeyAccepted"])
        self.assertFalse(result["wrongGenerationAccepted"])
        self.assertTrue(result["missingKeyFailsClosed"])


if __name__ == "__main__":
    unittest.main()
