# 📦 Node.js Setup Guide for macOS

**Last Updated**: November 9, 2025  
**Purpose**: Install Node.js and npm for React dashboard development

---

## 🚀 Quick Installation

### **Option 1: Homebrew (Recommended for macOS)**

```bash
# Install Homebrew (if not already installed)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Node.js (includes npm)
brew install node

# Verify installation
node --version
npm --version
```

### **Option 2: Official Installer**

1. **Download Node.js:**
   - Go to: https://nodejs.org/
   - Download LTS version (recommended)
   - Run the installer
   - Follow installation wizard

2. **Verify:**
   ```bash
   node --version
   npm --version
   ```

### **Option 3: Using nvm (Node Version Manager)**

```bash
# Install nvm
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.0/install.sh | bash

# Restart terminal or run:
source ~/.zshrc

# Install Node.js LTS
nvm install --lts

# Use it
nvm use --lts

# Verify
node --version
npm --version
```

---

## ✅ Verify Installation

After installation, verify everything works:

```bash
# Check Node.js version (should be 18+)
node --version

# Check npm version
npm --version

# Check installation location
which node
which npm
```

**Expected output:**
```
v18.17.0  (or higher)
9.6.7     (or higher)
/usr/local/bin/node  (or similar)
/usr/local/bin/npm   (or similar)
```

---

## 🎯 Install Dashboard Dependencies

Once Node.js is installed:

```bash
cd frontend
npm install
```

This will install all React dashboard dependencies.

---

## 🐛 Troubleshooting

### **"command not found: npm"**

**Solution**: Node.js isn't installed or not in PATH

1. **Check if Node.js is installed:**
   ```bash
   which node
   ```

2. **If not found, install using one of the methods above**

3. **If installed but not in PATH:**
   ```bash
   # Add to ~/.zshrc
   echo 'export PATH="/usr/local/bin:$PATH"' >> ~/.zshrc
   source ~/.zshrc
   ```

### **"Permission denied"**

**Solution**: Use Homebrew or fix npm permissions

```bash
# Option 1: Use Homebrew (recommended)
brew install node

# Option 2: Fix npm permissions
mkdir ~/.npm-global
npm config set prefix '~/.npm-global'
echo 'export PATH=~/.npm-global/bin:$PATH' >> ~/.zshrc
source ~/.zshrc
```

### **"EACCES: permission denied"**

**Solution**: Don't use sudo with npm

```bash
# Fix npm permissions (don't use sudo)
npm config set prefix ~/.npm-global
export PATH=~/.npm-global/bin:$PATH
```

---

## 📋 Requirements

- **Node.js**: 18.0.0 or higher
- **npm**: 9.0.0 or higher (comes with Node.js)

---

## 🔄 Update Node.js

### **If using Homebrew:**

```bash
brew upgrade node
```

### **If using nvm:**

```bash
nvm install --lts
nvm use --lts
```

### **If using official installer:**

Download and install latest from nodejs.org

---

## ✅ Quick Checklist

- [ ] Node.js installed (`node --version` works)
- [ ] npm installed (`npm --version` works)
- [ ] Version is 18+ (check with `node --version`)
- [ ] Can run `npm install` in frontend directory

---

## 🚀 Next Steps

After Node.js is installed:

1. **Install dashboard dependencies:**
   ```bash
   cd frontend
   npm install
   ```

2. **Start development server:**
   ```bash
   npm run dev
   ```

3. **Open dashboard:**
   - Go to http://localhost:3000

---

**Once Node.js is installed, you're ready to build the dashboard!** 🎯

