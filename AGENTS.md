# AGENTS.md — ai-dev-platform 發行儲存庫

開始任務前先讀取 `../ai-dev-platform/AGENTS.md` 與 `../ai-dev-platform/workflow/release.md`。

## 儲存庫邊界

- 只保存 `release-evidence/*.json`、`release-notes/*.md`、Git tag 與必要的儲存庫管理檔。
- 建置成品只保存在 CI／成品平台；本儲存庫只記錄不可變 URI 與 SHA-256。
- 不得加入產品原始碼、`external/`、第三方 skill、APK、AAB、韌體映像檔、ELF、ZIP 或其他建置成品。
- 變更完成後執行 `python3 ../ai-dev-platform/scripts/verify_release_layout.py .`。
