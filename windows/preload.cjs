const { contextBridge } = require("electron");
contextBridge.exposeInMainWorld("paxDesktop", { platform: process.platform, version: "0.2.0" });