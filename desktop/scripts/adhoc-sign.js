/**
 * Ad-hoc sign the packed app, as an electron-builder `afterPack` hook.
 *
 * Why this exists rather than letting electron-builder sign: distributing outside the App
 * Store needs a *Developer ID Application* certificate, and an Apple Development certificate
 * is not one — it signs builds for machines in your own provisioning profile and Gatekeeper
 * rejects it anywhere else. So there is no identity to sign with, and `identity: null` tells
 * electron-builder to skip signing entirely.
 *
 * Skipping is not the same as leaving it alone. On Apple Silicon every mach-O must carry
 * *some* valid signature to execute at all, and packaging rewrites the bundle — a new
 * Info.plist, a new icon, our resources — which invalidates the signature Electron shipped
 * with. An unsigned arm64 app does not warn, it fails to launch. So we re-sign ad-hoc.
 *
 * The frozen server in Resources/ is deliberately not touched: PyInstaller ad-hoc signs its
 * own binaries at build time, electron-builder only copies them, and those signatures are
 * still valid. Signing the bundle afterwards seals them into its CodeResources.
 *
 * What this does NOT do is get past Gatekeeper. An ad-hoc signature is not notarized, so the
 * first launch on someone else's Mac needs right-click → Open once. That is the trade for not
 * having a Developer ID; see RELEASE.md.
 */

const { execFileSync } = require("node:child_process");
const path = require("node:path");

exports.default = async function adhocSign(context) {
  if (context.electronPlatformName !== "darwin") return;

  const appPath = path.join(
    context.appOutDir,
    `${context.packager.appInfo.productFilename}.app`,
  );

  console.log(`[adhoc-sign] signing ${appPath}`);

  // --deep is the wrong tool for a real identity (it signs nested code with the outer
  // bundle's options instead of its own) but it is the right one here: every nested piece
  // wants the same thing, an ad-hoc signature, and there are hundreds of them.
  execFileSync("codesign", ["--force", "--deep", "--sign", "-", appPath], {
    stdio: "inherit",
  });

  // Verify rather than assume. A signature that did not take produces an app that launches
  // on this machine (where it was built) and dies on the machine it was built for.
  execFileSync("codesign", ["--verify", "--deep", "--strict", appPath], {
    stdio: "inherit",
  });

  console.log("[adhoc-sign] ok");
};
