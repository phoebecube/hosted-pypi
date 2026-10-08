# hosted-pypi

每週自動檢查上游，在 GitHub Actions 上編譯 **Windows x64** wheel，放進 GitHub Releases，再用 GitHub Pages 提供 pip 可以直接用的索引（PEP 503）。

倉庫裡只有工作流程和它們用到的腳本，不寫死任何版本號。

## 安裝

```bash
# TA-Lib
pip install ta-lib --extra-index-url https://<owner>.github.io/<repo>/simple/

# SageAttention2：CUDA 版本要跟你的 torch 一致（cu126 / cu130 / cu132 …）
pip install torch --index-url https://download.pytorch.org/whl/cu130
pip install sageattention --extra-index-url https://<owner>.github.io/<repo>/cu130/
```

`/simple/` 列出全部套件；`/cuXXX/` 只列出該 CUDA 版本的 wheel，跟 PyTorch 官方索引的分法一樣，pip 才會選到對的 CUDA 版本。

## 編譯什麼

每次執行都向上游查詢：

| 項目 | 來源 |
|------|------|
| Python | GitHub Actions 能裝的 Windows x64 最新 3 個穩定版（現在 3.12 / 3.13 / 3.14；3.15 正式版出來後自動變成 3.13 / 3.14 / 3.15） |
| TA-Lib | PyPI 上 `ta-lib` 最新版；TA-Lib C 函式庫取最新 tag |
| SageAttention2 | [woct0rdho/SageAttention](https://github.com/woct0rdho/SageAttention) 最新 `v*-windows*` tag |
| CUDA 組合 | PyPI 最新 torch 在 Windows 上提供的所有 `cuXXX`；CUDA 工具包取同版號最新修補版 |

- TA-Lib：每個 Python 版本各編一個 wheel。
- SageAttention2：每個 `cuXXX` 各編一個。上游是 abi3（`cp310-abi3`）時，一個 wheel 就適用 Python 3.10 以上全部版本，所以每個 CUDA 只編一次；上游若不是 abi3，會自動改成每個 Python 各編一次。
- 預設 GPU 架構 `8.0 8.6 8.9 9.0 12.0`（RTX 30 / 40 / 50、A100、H100），在 `sageattention.yml` 的 `SAGE_ARCHS` 修改；該 CUDA 版本不支援的架構會自動略過。

為什麼用 woct0rdho 的分支：官方 thu-ml/SageAttention 的 setup.py 只支援 GCC，Windows 的 MSVC 編不起來；這個分支持續跟進官方，並修好了 Windows 編譯。

## 工作流程

| 檔案 | 用途 |
|------|------|
| `talib.yml` | TA-Lib：查版本 → 只編 Release 裡缺的 Python 版本 → 上傳 |
| `sageattention.yml` | SageAttention2：查版本 → 只編 Release 裡缺的 CUDA 組合 → 上傳 |
| `publish.yml` | 共用：把 wheel 加進 Release（`talib-v<版本>`、`sageattention-v<版本>`），再更新索引 |
| `update-index.yml` | 掃描所有 Release，產生 Pages 索引（含 sha256） |

| 腳本 | 用途 |
|------|------|
| `.github/scripts/plan.py` | 查上游版本、比對 Release 已有的 wheel，產生編譯矩陣 |
| `.github/scripts/install_cuda.py` | 只從 NVIDIA redist 下載 nvcc / cudart / CCCL（CUDA 13 另含 CRT、NVVM），約 100 MB |
| `.github/scripts/generate_index.py` | 產生 `/simple/` 和 `/cuXXX/` 索引 |

每週一自動執行（只在預設分支），也可以在 Actions 頁面手動執行；勾選 `force` 會全部重新編譯。

## 編譯效率

- 上游沒變化時只跑一個幾十秒的 Ubuntu 檢查 job，不開 Windows 機器。
- 只補編 Release 裡還沒有的組合。
- 各 Python / 各 CUDA 版本平行編譯，其中一個失敗不會擋住其他的上傳。
- TA-Lib C 函式庫依版本快取，只編一次。
- CUDA 不跑官方安裝程式，只下載編譯需要的元件。

## 第一次設定

- Settings → Pages → Source 選 `GitHub Actions`。
- `github-pages` environment 如果要審批，拿掉 Required reviewers。

## License

MIT
