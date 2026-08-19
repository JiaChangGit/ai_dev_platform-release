# Release Note

每個正式版本保存一份 `<version>.md`，格式依平行目錄 `ai-dev-platform/templates/release-note.md`。

第一個非空白行必須是一級標題，並包含相同版本：

```markdown
# AI Dev Platform v<version>
```

內容應包含：

- 版本摘要
- 新增功能與修正
- 破壞性變更與升級步驟
- 已知問題
- 成品不可變 URI 與 SHA-256
- 對應的 release evidence 路徑

Release Note 不貼入 Token、內部憑證、完整 CI log 或建置成品。變更內容應根據已合併 PR 與發行證據整理，不直接複製未審查的對話紀錄。
