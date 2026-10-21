// MV3 service worker：点击扩展图标时若无 popup 也可走这里；当前用 popup。
chrome.runtime.onInstalled.addListener(() => chrome.storage.sync.set({base: "http://127.0.0.1:8800"}));
