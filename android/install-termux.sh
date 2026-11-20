#!/data/data/com.termux/files/usr/bin/sh
# Install trainnet in Termux. Run from the cloned folder:  sh internet-train-coverage/android/install-termux.sh
set -e
cd "$(dirname "$0")/.."

pkg update -y
pkg install -y python termux-api sqlite
pip install --upgrade .            # recording needs no compiled dependencies

# Shared storage, so exported trips can be copied off the phone (asks for permission once)
[ -d "$HOME/storage/shared" ] || termux-setup-storage

# One-tap start from the home screen (needs the optional Termux:Widget app)
mkdir -p "$HOME/.shortcuts"
cat > "$HOME/.shortcuts/Train record" <<'EOF'
#!/data/data/com.termux/files/usr/bin/sh
trainnet
EOF
chmod +x "$HOME/.shortcuts/Train record"

echo
echo "Checking Termux:API (approve the permission pop-ups on the phone):"
echo "- wifi:";     termux-wifi-connectioninfo  || true
echo "- cell:";     termux-telephony-cellinfo | head -c 400 || true; echo
echo "- gps (up to 60 s, go near a window):"
timeout 60 termux-location -p gps -r once || echo "no fix yet; that is fine indoors"
echo
echo "Done. Type  trainnet  (or tap the 'Train record' widget): the page opens in the browser."
