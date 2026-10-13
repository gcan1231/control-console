# 总控制台

项目仓库，使用 Git 管理版本，GitHub 保存远程备份。

## 日常使用

```powershell
# 查看修改
git status

# 提交修改（将文件名替换为实际修改的文件）
git add <文件名>
git commit -m "说明本次修改"

# 同步到 GitHub
git push
```

主分支为 `main`。新功能可使用独立分支开发：

```powershell
git switch -c feature/功能名称
```

请勿提交密码、访问令牌或真实的 `.env` 配置。需要示例配置时，使用不含敏感信息的 `.env.example`。
