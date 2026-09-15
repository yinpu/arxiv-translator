# 本地编译错误速查

## 读取日志

脚本会在 stderr 打印构建目录和完整日志路径：

- `$WORK_DIR/.arxiv-build/run-*/attempt-*.log`：latexmk、LaTeX 和文献工具的完整输出。
- `attempt-*/<主文件名>.log`：该次构建的最终 LaTeX 日志。
- `attempt-*/<主文件名>.blg`：BibTeX/Biber 日志（如运行）。

先处理第一个 `!` 错误或 `文件:行号:` 报错，后续错误通常是连锁反应。失败不会覆盖已有目标 PDF，源码与日志默认保留。脚本从构建副本运行，修复应写回 `$WORK_DIR` 中对应文件。

## 缺少本地工具

`Missing local tool: latexmk` / `lualatex` / `xelatex`：安装完整 TeX Live。macOS 使用：

```bash
brew install --cask mactex-no-gui
```

脚本优先查找 PATH，并自动兼容 `/Library/TeX/texbin`。不要改用远程服务。

## 字体未找到或 PDF 缺字

`Font "XXX" not found` / `Missing character:`：源码指定的字体未在本机安装，或不包含所需字符。

自动注入的中文配置使用 TeX Live 自带的 Fandol，可检查：

```bash
kpsewhich FandolSong-Regular.otf
kpsewhich FandolHei-Regular.otf
kpsewhich FandolFang-Regular.otf
```

若命令不在 PATH，macOS 用 `/Library/TeX/texbin/kpsewhich`。已有中文字体配置不会自动替换；确认字体意图后安装所需字体或调整为可用字体。罕见汉字可能超出 Fandol 字库，需要另选覆盖相应字符的字体。PDF 文本提取乱码不一定是缺字，可用 `pdftoppm -png <PDF> <输出前缀>` 渲染检查。

## 宏包缺失

`File 'xxx.sty' not found`：先检查该文件是否是论文自带文件、相对路径是否正确，再用 `kpsewhich xxx.sty` 查询本地安装。TeX Live 可使用 `tlmgr search --global --file '/xxx.sty'` 查找所属包，并用 `tlmgr install <包名>` 安装。系统级安装可能需要管理员权限。安装依赖需要联网，编译本身不联网。

不要仅为通过编译而随意删除宏包，它可能影响公式和排版。

## 编码宏包冲突

XeLaTeX/LuaLaTeX 的 Unicode 编译栈原生支持 UTF-8。脚本会注释主文件里独立声明的 `inputenc` / `fontenc`；若声明混在多个宏包中、或藏在模板文件里，按具体错误修复，保留其他宏包。

## 宏重复定义

`LaTeX Error: Command \xxx already defined`：常见于 `luatexja` 和论文宏冲突。脚本只针对日志指出的宏，将源码中的 `\newcommand` 改为 `\renewcommand`，最多自动重试两次。其他错误需根据日志处理，不要无限重试。

## 引用未解析与参考文献

脚本使用 `latexmk` 多轮编译，并检查最终日志。未定义的引用、仍需重跑的标签和缺字会导致交付失败，即使引擎已生成 PDF。

- 有普通 BibTeX `.bbl`：优先内联预置文献，避免缺少 `.bib` 时无法生成引用。
- 只有 `.bib`：确认 `\bibliography`、`\bibliographystyle` 和引用键正确，由 latexmk 调用 BibTeX。
- biblatex：确认 `\addbibresource` 指向正确文件，通常由 Biber 处理。预置 `.bbl` 且 `.bib` 缺失时禁用文献重生成，保护原始 `.bbl`；若 `.bbl` 与本机 biblatex 版本不兼容，需要匹配版本或补齐 `.bib`。
- `??` / `[?]`：检查引用键、标签拼写以及首个编译错误，不要把 PDF 中合法的问号文本当成引用错误。

## 图片或章节找不到

编译以 `WORK_DIR` 为根目录，`MAIN_TEX` 保留相对路径。图片和 `\input` 路径应相对该根目录。如果项目的相对路径原本基于主文件所在目录，请将该目录作为 `WORK_DIR`。所有 PDF 图片都会复制到构建目录，不会根据文件名猜测并删除。

## 超时或编译缓慢

每次 latexmk 调用最多 300 秒，超时后会停止编译进程；macOS/Linux 同时终止它启动的子进程。查看日志末尾，区分首次字体缓存生成、复杂图形和源码循环。构建副本默认保留，确认不再需要后随 `.tmp_arxiv` 一并清理。

## 模板依赖 shell escape 或 latexmk 配置

脚本不加载 latexmk 配置文件，并显式关闭 shell escape。涉及 minted、外部图形转换等流程时，先单独生成所需资源，再本地编译；不要自动启用源码附带的任意命令。

## 中文溢出

根据实际版面，在 preamble 添加 `\setlength{\emergencystretch}{3em}` 并检查长 URL、表格列宽和不可断行内容；必要时局部使用 `\sloppy`，重新渲染确认排版。
