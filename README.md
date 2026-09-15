# arxiv-translator · 本地编译版

将 arXiv 论文的 LaTeX 源码翻译为中文，并在本机编译成 PDF。

本仓库由 [yinpu](https://github.com/yinpu) 维护，项目地址为 [yinpu/arxiv-translator](https://github.com/yinpu/arxiv-translator)。在原有论文翻译流程上，本版本改为使用本地 TeX Live 编译：

- 使用 `latexmk` 管理 LuaLaTeX/XeLaTeX 和参考文献的多轮编译。
- 编译时不上传源码，不回退到远程服务。
- 自动添加的中文配置使用 TeX Live 自带字体。
- 编译失败保留已有 PDF，完整日志和工作目录便于继续修订。

Skill 的工作流程与翻译规则见 [SKILL.md](arxiv-translator/SKILL.md)。

## 这个 Skill 是做什么的？ 🔧 

在支持 Agent Skills 的环境里（例如 Codex、Claude Code、Cursor 等），安装本 Skill 后，你可以直接说明意图，例如：

- 「帮我把 1706.03762 翻译成中文 PDF」/「翻译这篇 arXiv：https://arxiv.org/abs/…」
- 「Limo: Less is more for reasoning 有中文版吗？给我翻译一版」/「我想读 xxx 这篇的中文版」
- 多篇时可说：「翻译 2501.12948 和 Attention Is All You Need」

核心是：给出能唯一定位论文的信息（ID、arXiv 链接或论文标题），并传达出翻译的目的；Agent 会自动基于本 Skill 里的步骤执行。

> 注：为节省 token 与处理时间，默认仅翻译正文部分；若需要全文翻译，请告知模型「翻译全文，包含附录」。

Skill 会引导 Agent：

1. 拉取 arXiv LaTeX；无源码则说明并跳过。
2. 翻译正文，公式、引用、标签、图表路径与常用学术英文词保留不译。
3. 主文件 `\begin{document}` 前插入编译所需库与中文支持，并复用 arXiv 源码自带的 `.bbl` 以保证引用可解析。
4. `scripts/compile.py` 调用本地 `latexmk` 和 LuaLaTeX（已有 xeCJK 配置时使用 XeLaTeX），自动完成多轮编译并生成 PDF。编译成功后默认保留 `.tmp_arxiv` 工作目录，便于检查 PDF 后继续微调；确认无误后可再调用 `cleanup.py` 清理。

本地需要 Python 3.9+ 和完整 TeX Live 环境，Python 脚本不需要 `requests`。编译完全在本机进行，不上传源码，也不会回退到远程服务；论文检索、源码下载仍需要联网。

### 配置本地编译环境

macOS 安装完整编译环境：

```bash
brew install --cask mactex-no-gui
```

安装完成后可重新打开终端，或直接验证：

```bash
/Library/TeX/texbin/latexmk -v
/Library/TeX/texbin/lualatex --version
/Library/TeX/texbin/xelatex --version
/Library/TeX/texbin/bibtex --version
/Library/TeX/texbin/biber --version
/Library/TeX/texbin/kpsewhich luatexja-fontspec.sty
/Library/TeX/texbin/kpsewhich FandolSong-Regular.otf
```

脚本优先查找 PATH，也会自动查找 `/Library/TeX/texbin`。其他系统安装完整 TeX Live，并把其可执行文件目录加入 PATH。

调用方式保持不变：

```bash
python3 arxiv-translator/scripts/compile.py "$WORK_DIR" "$MAIN_TEX" "$OUTPUT_DIR/$PDF_NAME.pdf"
```

`MAIN_TEX` 是相对 `WORK_DIR` 的路径，也可以是其内部的绝对路径；源码中的图片、章节路径以 `WORK_DIR` 为基准。输出参数可指定 PDF 文件或已有目录，也可用末尾 `/` 表示待创建的目录。支持中文和空格路径。

脚本在 `$WORK_DIR/.arxiv-build/run-*/attempt-*` 中构建源码副本，完整控制台日志保存在对应的 `attempt-*.log`，TeX 日志在构建副本内。每次运行最多 300 秒，明确可修复的宏重复定义错误最多自动重试两次。成功后检查 PDF 完整性、最终日志中的未解析引用和缺字，再原子替换目标 PDF；失败保留已有 PDF、源码和日志。

自动添加的中文配置使用 TeX Live 自带的 Fandol 字体；已有中文配置予以保留。普通 BibTeX 文档优先复用源码自带 `.bbl`；没有预置文献时由 `latexmk` 调用 BibTeX/Biber。出错时参考 `arxiv-translator/references/compile-errors.md`。构建不加载 latexmk 配置文件，并关闭 shell escape；需要特殊外部工具的论文应先准备好对应资源。


## 安装方式 💻 

先按上文配置本地编译环境，再安装 Skill。

1. Clone 本仓库：

   ```bash
   git clone https://github.com/yinpu/arxiv-translator.git
   cd arxiv-translator
   ```

   注：若 Git 不可用，可直接下载压缩包解压。

2. 打开你常用的 CLI / IDE Agent 对话，发送：

   ```text
   路径 `<path>` 中定义了一个 Skill，请你阅读并将其安装到你的 skills 目录下。
   ```
   
   注：将 `<path>` 替换为仓库内包含 `SKILL.md` 的子目录绝对路径，即 `<仓库路径>/arxiv-translator`。

以下安装与排版截图沿用自上游项目，展示 Skill 的使用方式和译文效果；本版本的编译方式以上文的本地编译说明为准。

以 Codex 为例，安装过程如下：

![Codex 安装示例](images/Install_Examples-Codex.png)

英文原版与中文译文的 PDF 页面对比，版式与公式结构保持一致，非必要内容不进行翻译，实现了对学术专有名词的保留，同时适配各种不同的论文模板：

![翻译前后 PDF 对比（节选1）](images/Showcase_1.png)
![翻译前后 PDF 对比（节选2）](images/Showcase_2.png)
![翻译前后 PDF 对比（节选3）](images/Showcase_3.png)
## 和「直接把 PDF 丢给翻译」相比，优势在哪里？ 📊

| 维度 | 直接翻译 PDF | 本 Skill（LaTeX 源码路径） |
|------|----------------|-----------------------------|
| 版面与公式 | 整页/OCR 易乱版，公式与多栏易坏 | 在源码里保留数学与引用，再编译成正常 PDF |
| 翻译粒度 | 常按页切块，和章节结构脱节 | 按标题、摘要、正文等结构译，细则见 [SKILL.md](arxiv-translator/SKILL.md) |
| 上下文与译文质量 | 切块输入，语境窄，术语与指代易不一致 | 能利用更大上下文，论证与术语更易统一 |
| 可复核性 | 难按原结构改 | 产出 `.tex`，方便 diff 与局部重译 |
| 依赖环境 | 各家工具形态不一 | Python 3 与本地 TeX Live；源码无需上传编译服务 |

当然，本 Skill 也有限制：仅适用于 arXiv 上提供 LaTeX 源码的稿件；纯 PDF 投稿则无法沿用同一流程。

## 仓库结构 📂

```
arxiv-translator/
├── README.md
├── LICENSE
├── arxiv-translator/        # 安装此目录作为 Skill
│   ├── SKILL.md             # Agent 实际读取的工作流程与翻译规则
│   ├── scripts/
│   │   ├── download.py      # 按 arXiv ID 下载源码并解压
│   │   ├── inspect_tex.py   # 检查可能未翻译的英文片段
│   │   ├── compile.py       # 本地多轮编译，验证并写出 PDF
│   │   └── cleanup.py       # 清理工作目录，可选备份 .tex/.bbl
│   └── references/
│       └── compile-errors.md
├── tests/                   # 回归测试与真实编译测试
└── images/                  # 上游项目的安装与译文示例截图
```

## 开发验证

在仓库根目录运行 `python3 -m unittest discover -s tests -v`。回归测试不需要 TeX；真实编译测试在缺少 `latexmk` 时跳过，安装后会验证中文、图片、交叉引用、BibTeX/Biber 和 XeLaTeX。设置 `ARXIV_TEST_OUTPUT_DIR` 可保留真实测试的 PDF 与日志，便于渲染检查。

## 来源与致谢

本项目基于 [Leey21/arxiv-translator](https://github.com/Leey21/arxiv-translator) 改进，沿用了上游的论文翻译流程与示例截图。感谢原作者的工作。本仓库使用 [MIT License](LICENSE)，保留上游版权声明。

本地编译依赖 [TeX Live](https://tug.org/texlive/)、[MacTeX](https://tug.org/mactex/) 和 [latexmk](https://ctan.org/pkg/latexmk)。感谢这些项目提供的排版引擎、宏包与自动编译工具。

---

如有问题或改进建议，欢迎前往 [yinpu/arxiv-translator 的 Issues](https://github.com/yinpu/arxiv-translator/issues) 反馈。
