# AGENTS.md — ai_dev_platform-release 發行儲存庫

開始任務前先讀取：

1. `../ai-dev-platform/AGENTS.md`
2. `../ai-dev-platform/workflow/release.md`
3. `../ai-dev-platform/docs/release-evidence.md`
4. 本儲存庫 `README.md`

## 儲存庫邊界

- 只保存 `release-evidence/*.json`、`release-notes/*.md`、Git tag 與必要的儲存庫管理檔。
- 建置成品保存在 CI／成品平台；本儲存庫只記錄不可變 URI 與 SHA-256。
- 不得加入平台原始碼、`external/`、第三方 skill、ZIP、APK、AAB、韌體映像檔、ELF、簽章、SBOM、SLSA 實體檔或私鑰。
- 本儲存庫使用獨立 `.git`，remote 必須是 `JiaChangGit/ai_dev_platform-release`，不得指向平台維護儲存庫。
- 若本機 `.git` 已移除，只能連接同名的空白遠端重新初始化；遠端已有歷史時必須重新 clone，不得 force push。

## 每次變更必做

```bash
python3 -B ../ai-dev-platform/scripts/verify_release_layout.py .
python3 -B ../ai-dev-platform/scripts/verify_release_evidence.py \
  release-evidence/<version>.json
python3 -B scripts/manage_collaborators.py check
git diff --check
```

`verify_release_evidence.py` 只驗證 JSON 契約。正式發布前，必須依 README 執行 `verify_release_readiness.py`，並檢查實體成品 SHA-256、GitHub attestation（或相容 OpenSSL 簽章）、SBOM、SLSA、來源 commit、乾淨工作樹與 tag。驗證程式不會自行判定 reviewer 是否獨立；發布者還要在 GitHub 確認 CI run 與獨立核准紀錄。

## 禁止事項

- 不得因 layout 或 readiness 失敗而降低驗證條件。
- 不得 force push `main` 或移動已發布 tag。
- 不得把 Token 寫入檔案、Git remote URL、CI 範本或命令列參數。
- 不得在 PR 分支推送正式 tag；若使用 squash merge，正式 tag 必須在合併後的 `main` 建立並重新驗證。
- Collaborator 或審查政策變更後，必須執行 `scripts/manage_collaborators.py check`，並確認遠端 branch protection 實際生效。
