# iOS 客户端

原生 SwiftUI，iOS 17+，无第三方依赖。它只是本地服务的控制台——不含任何适配器实现，
端点和模型完全由服务端返回的配置决定。

## 运行

先按仓库根目录的 README 启动本地服务，然后在 Xcode 打开 `Orchestragent.xcodeproj`，
选择 `Orchestragent` scheme 在模拟器运行。默认连接 `http://localhost:8765`。

## 界面

- 单一调度对话：一句话提交、历史列表、可折叠的阶段详情
- 顶部状态条显示当前模式与费用；费用未知时显示「未知」，不把未知显示成 $0
- 审批卡展示操作原文、完整 SHA-256 摘要与版本，按钮提交的正是卡片上的摘要
- 调度配置：模型、全局要求、各端点偏好；一句话调整先生成预览，确认后才应用
- 提交前把请求 UUID 与完整请求写入 `UserDefaults`，断线后可重试同一请求
- 前台每秒同步、断线每三秒重连；切到后台只停止轮询，服务端任务继续运行

服务端没有注册真实适配器时，界面会停留在 Mock 模式，真实执行入口不可用。

## 构建与测试

```sh
xcodebuild -project ios/Orchestragent.xcodeproj -scheme Orchestragent \
  -sdk iphonesimulator -configuration Debug -derivedDataPath .build/ios \
  CODE_SIGNING_ALLOWED=NO build

xcodebuild -project ios/Orchestragent.xcodeproj -scheme Orchestragent \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  -derivedDataPath .build/ios CODE_SIGNING_ALLOWED=NO test
```

单元测试使用 `URLProtocol` 桩，不依赖服务端或任何账户。另有 2 项 localhost 集成测试，
在本地 Mock 服务未启动时自动跳过。

真机连接、认证 TLS、推送通知与后台持续联网尚未实现。
