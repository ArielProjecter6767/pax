const { app, BrowserWindow } = require("electron");
const path = require("node:path");
function createWindow() {
  const win = new BrowserWindow({ width: 1250, height: 820, minWidth: 900, minHeight: 600, backgroundColor: "#090b12", webPreferences: { preload: path.join(__dirname, "preload.cjs"), contextIsolation: true, nodeIntegration: false } });
  win.loadFile(path.join(__dirname, "index.html"));
}
app.whenReady().then(() => { createWindow(); app.on("activate", () => { if (!BrowserWindow.getAllWindows().length) createWindow(); }); });
app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });