# 發行證據

每個正式版本保存一份 `<version>.json`，格式依平行目錄 `ai-dev-platform/distribution/release-evidence.schema.json`。

## 必要內容

- 完整來源 commit SHA 與允許的 `refs/heads/main`、`refs/heads/release/*` 或 `refs/tags/v*`。
- 不可變成品 URI、SHA-256、GitHub attestation bundle（或相容簽章）的 URI／SHA-256 與簽署身分。
- CI system、run ID，以及 `build`、`test`、`lint`、`security`、`package`。
- SPDX／CycloneDX SBOM 與 SLSA provenance URI／SHA-256。
- 不同的核准者與發布者。

不得填入 Token、私鑰、內部帳號密碼或可變的 `latest`／`current`／`snapshot`／`nightly` URI。

```bash
python3 -B ../ai-dev-platform/scripts/verify_release_evidence.py \
  release-evidence/<version>.json
```

此命令只驗證 JSON 契約；正式發布仍須依根目錄 README 執行 `verify_release_readiness.py`。

契約驗證器不會連線查詢 CI run、成品 URI 或帳號身分。GitHub attestation readiness 會以 `gh attestation verify` 固定來源 repository、workflow、commit、ref 與 predicate，但發布者仍須在 GitHub 確認 run 確實成功，且核准者是有效的獨立人員；不能只在 JSON 填入名稱與 check 字串。
