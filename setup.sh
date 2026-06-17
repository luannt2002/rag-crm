#!/bin/bash
# ============================================================
#  LINUX MINT — DEV SETUP SCRIPT
#  Chạy: chmod +x setup_dev_linux_mint.sh && ./setup_dev_linux_mint.sh
# ============================================================

set -e
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }

log "Cập nhật hệ thống..."
sudo apt update && sudo apt upgrade -y

# ── Công cụ nền tảng ────────────────────────────────────────
log "Cài công cụ nền tảng..."
sudo apt install -y \
  curl wget build-essential ca-certificates gnupg \
  software-properties-common apt-transport-https \
  unzip zip htop net-tools

# ── Git: kiểm tra trước, chỉ cài nếu chưa có ───────────────
if command -v git &>/dev/null; then
  log "Git đã được cài: $(git --version) — bỏ qua"
else
  log "Cài Git..."
  sudo apt install -y git
fi
warn "Nhớ chạy: git config --global user.name 'Tên bạn'"
warn "Nhớ chạy: git config --global user.email 'email@example.com'"

# ── Tiếng Việt — IBus-Bamboo ────────────────────────────────
log "Cài IBus-Bamboo (gõ tiếng Việt)..."
sudo add-apt-repository -y ppa:bamboo-engine/ibus-bamboo
sudo apt update
sudo apt install -y ibus-bamboo
warn "Sau khi xong: vào Settings → Input Method → chọn IBus-Bamboo"

# ── Trình duyệt ─────────────────────────────────────────────
log "Cài Google Chrome..."
wget -q -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo dpkg -i /tmp/chrome.deb || sudo apt -f install -y

log "Cài Cốc Cốc..."
wget -q -O /tmp/coccoc.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
# Cốc Cốc: tải thủ công tại https://coccoc.com/linux nếu link thay đổi
warn "Cốc Cốc: tải thủ công tại https://coccoc.com/linux rồi: sudo dpkg -i coccoc_*.deb"

# ── Python ──────────────────────────────────────────────────
log "Cài Python 3..."
sudo apt install -y python3 python3-pip python3-venv python3-dev

# ── Java (OpenJDK 25) ───────────────────────────────────────
log "Cài Java 25 (OpenJDK)..."
# Java 25 chưa có trong apt chính thức → dùng PPA hoặc tải từ adoptium
sudo add-apt-repository -y ppa:openjdk-r/ppa 2>/dev/null || true
sudo apt update
if apt-cache show openjdk-25-jdk &>/dev/null; then
  sudo apt install -y openjdk-25-jdk
else
  warn "Java 25 chưa có trên apt → cài từ Adoptium (Temurin 25)..."
  wget -q -O /tmp/temurin.tar.gz \
    "https://api.adoptium.net/v3/binary/latest/25/ga/linux/x64/jdk/hotspot/normal/adoptium?project=jdk"
  sudo mkdir -p /usr/lib/jvm/temurin-25
  sudo tar -xzf /tmp/temurin.tar.gz -C /usr/lib/jvm/temurin-25 --strip-components=1
  sudo update-alternatives --install /usr/bin/java java /usr/lib/jvm/temurin-25/bin/java 25
  sudo update-alternatives --install /usr/bin/javac javac /usr/lib/jvm/temurin-25/bin/javac 25
  sudo update-alternatives --set java /usr/lib/jvm/temurin-25/bin/java
fi
log "Java: $(java -version 2>&1 | head -1)"

# ── Node.js via NVM ─────────────────────────────────────────
log "Cài NVM + Node.js LTS..."
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
export NVM_DIR="$HOME/.nvm"
# shellcheck source=/dev/null
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
nvm install --lts
nvm use --lts
log "Node $(node -v) — npm $(npm -v)"

# ── Go (phiên bản mới nhất) ─────────────────────────────────
log "Cài Go lang (lấy version mới nhất từ go.dev)..."
GO_VERSION=$(curl -fsSL "https://go.dev/VERSION?m=text" | head -1 | sed 's/go//')
log "Go version: ${GO_VERSION}"
wget -q -O /tmp/go.tar.gz "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz"
sudo rm -rf /usr/local/go
sudo tar -C /usr/local -xzf /tmp/go.tar.gz
# Thêm vào PATH nếu chưa có
if ! grep -q '/usr/local/go/bin' "$HOME/.bashrc"; then
  echo 'export PATH=$PATH:/usr/local/go/bin' >> "$HOME/.bashrc"
fi
warn "Chạy 'source ~/.bashrc' hoặc mở terminal mới để dùng go"

# ── Docker ──────────────────────────────────────────────────
log "Cài Docker..."
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu jammy stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker "$USER"
warn "Docker: cần logout & login lại để dùng không cần sudo"

# ── VS Code ─────────────────────────────────────────────────
log "Cài VS Code..."
wget -q -O /tmp/vscode.deb \
  "https://code.visualstudio.com/sha/download?build=stable&os=linux-deb-x64"
sudo dpkg -i /tmp/vscode.deb || sudo apt -f install -y

# ── JetBrains Toolbox (IntelliJ CE, PyCharm CE...) ──────────
log "Cài JetBrains Toolbox..."
TOOLBOX_URL=$(curl -s "https://data.services.jetbrains.com/products/releases?code=TBA&latest=true&type=release" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['TBA'][0]['downloads']['linux']['link'])" 2>/dev/null \
  || echo "")
if [ -n "$TOOLBOX_URL" ]; then
  wget -q -O /tmp/toolbox.tar.gz "$TOOLBOX_URL"
  sudo tar -xzf /tmp/toolbox.tar.gz -C /opt
  TOOLBOX_BIN=$(find /opt -name "jetbrains-toolbox" -type f 2>/dev/null | head -1)
  [ -n "$TOOLBOX_BIN" ] && "$TOOLBOX_BIN" &
  log "JetBrains Toolbox đã chạy — cài IntelliJ IDEA CE / PyCharm CE từ giao diện"
else
  warn "Không lấy được link Toolbox tự động. Tải tại: https://www.jetbrains.com/toolbox-app/"
fi

# ── Postman ─────────────────────────────────────────────────
log "Cài Postman (snap)..."
sudo snap install postman 2>/dev/null || warn "snap chưa sẵn sàng, thử: sudo snap install postman"

# ── Telegram ────────────────────────────────────────────────
log "Cài Telegram (flatpak)..."
sudo apt install -y flatpak
flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install -y flathub org.telegram.desktop

# ── UltraViewer ─────────────────────────────────────────────
warn "UltraViewer: tải bản Linux tại https://www.ultraviewer.net → dpkg -i ultraviewer_*.deb"

# ── Công cụ dev bổ sung ─────────────────────────────────────
log "Cài thêm công cụ dev hữu ích..."
sudo apt install -y \
  httpie \        # HTTP client đẹp hơn curl
  jq \            # xử lý JSON trên terminal
  tree \          # hiển thị cây thư mục
  tmux \          # terminal multiplexer
  neovim          # editor terminal mạnh

# lazygit
LAZYGIT_VERSION=$(curl -s "https://api.github.com/repos/jesseduffield/lazygit/releases/latest" | grep -Po '"tag_name": "v\K[^"]*')
curl -Lo /tmp/lazygit.tar.gz "https://github.com/jesseduffield/lazygit/releases/download/v${LAZYGIT_VERSION}/lazygit_${LAZYGIT_VERSION}_Linux_x86_64.tar.gz"
sudo tar xf /tmp/lazygit.tar.gz -C /usr/local/bin lazygit
log "lazygit $(lazygit --version | head -1)"

# ── DBeaver CE (SQL client) ─────────────────────────────────
log "Cài DBeaver CE..."
wget -q -O /tmp/dbeaver.deb \
  "https://dbeaver.io/files/dbeaver-ce_latest_amd64.deb"
sudo dpkg -i /tmp/dbeaver.deb || sudo apt -f install -y

# ── mkcert (HTTPS local) ────────────────────────────────────
log "Cài mkcert..."
sudo apt install -y libnss3-tools
wget -q -O /usr/local/bin/mkcert \
  https://dl.filippo.io/mkcert/latest?for=linux/amd64
sudo chmod +x /usr/local/bin/mkcert
mkcert -install

# ── Dọn dẹp ────────────────────────────────────────────────
log "Dọn dẹp..."
sudo apt autoremove -y
rm -f /tmp/*.deb /tmp/*.tar.gz /tmp/*.gz

echo ""
echo -e "${GREEN}======================================================"
echo -e "  XONG! Các bước cần làm thủ công:"
echo -e "======================================================"
echo -e "  1. source ~/.bashrc   (load Go + NVM vào PATH)"
echo -e "  2. Logout & login lại (để dùng Docker không cần sudo)"
echo -e "  3. Settings → Input Method → chọn IBus-Bamboo"
echo -e "  4. Tải Cốc Cốc: https://coccoc.com/linux"
echo -e "  5. Tải UltraViewer: https://www.ultraviewer.net"
echo -e "  6. Mở JetBrains Toolbox → cài IntelliJ IDEA CE"
echo -e "  7. git config --global user.name / user.email"
echo -e "${NC}"