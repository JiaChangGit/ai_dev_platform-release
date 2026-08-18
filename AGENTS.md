# AGENTS.md — ai_dev_platform-release 發行儲存庫

開始任務前先讀取 `../ai-dev-platform/AGENTS.md` 與 `../ai-dev-platform/workflow/release.md`。

## 儲存庫邊界

- 只保存 `release-evidence/*.json`、`release-notes/*.md`、Git tag 與必要的 repository 管理檔；管理檔限於允許清單內的 CI、CODEOWNERS、PR／MR 範本與 collaborator 腳本。
- 建置成品只保存在 CI／成品平台；本儲存庫只記錄不可變 URI 與 SHA-256。
- 不得加入產品原始碼、`external/`、第三方 skill、APK、AAB、韌體映像檔、ELF、ZIP 或其他建置成品。
- 本儲存庫使用獨立 `.git`，remote 必須是 `JiaChangGit/ai_dev_platform-release`，不得指向平台維護儲存庫。
- 若本機 `.git` 已移除，只能連接同名的空白遠端重新初始化；遠端已有歷史時必須重新 clone，不得 force push。
- 變更完成後執行 `python3 ../ai-dev-platform/scripts/verify_release_layout.py .`。
- collaborator 或審查政策變更後，另執行 `python3 -B scripts/manage_collaborators.py check`；管理 Token 不得寫入檔案或 CI 變數範本。
