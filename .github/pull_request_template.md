## 發行項目

- 版本：
- 對應 tag：
- CI run：

## 變更內容

- [ ] `release-evidence/<version>.json`
- [ ] `release-notes/<version>.md`
- [ ] 沒有加入原始碼、skill 或建置成品

## 驗證

- [ ] `python3 -B scripts/manage_collaborators.py check`
- [ ] `python3 -B ../ai-dev-platform/scripts/verify_release_layout.py .`
- [ ] 成品 URI 不可變，SHA-256 已重新計算並比對
- [ ] SBOM、來源證明與簽章欄位均已驗證

## 審查

- [ ] reviewer 不是本次變更作者
- [ ] 所有對話已解決
