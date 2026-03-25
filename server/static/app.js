const canvas = document.getElementById('deviceScreen');
const ctx = canvas.getContext('2d');
const treeContainer = document.getElementById('tree-container');
const propsContainer = document.getElementById('props-container');
const loading = document.getElementById('loading');

// 存储设备和显示信息
let devicesList = [];
let displaysList = [];
let currentDevice = null;
let currentDisplay = "0";

let rootNode = null;
let selectedNode = null;
let hoverNode = null; // New for hover
let screenImage = new Image();
let mapNodeToDom = new Map();

// Accessibility status cache
let accessibilityStatus = {
    enabled: false,
    running: false,
    checkedAt: 0
};

// Header status rendering (base text + multiple tags)
let statusBaseText = '未连接';
const statusTags = new Map(); // key -> string (without brackets)

function renderStatus() {
    const statusEl = document.getElementById('status');
    if (!statusEl) return;
    const tags = Array.from(statusTags.values()).filter(Boolean).map(t => `[${t}]`).join(' ');
    statusEl.innerText = tags ? `${statusBaseText} ${tags}` : statusBaseText;
}

// Settings state - 每个关键字都有独立的颜色配置
let searchSettings = {
    patterns: [],  // 每个元素是 { text, foreColor, backColor }
    ignoreCase: true
};

// 预设颜色方案（用于自动分配）
const colorPresets = [
    { foreColor: '#60a5fa', backColor: '#1e3a5f' },  // 蓝色
    { foreColor: '#f59e0b', backColor: '#78350f' },  // 橙色
    { foreColor: '#10b981', backColor: '#064e3b' },  // 绿色
    { foreColor: '#ef4444', backColor: '#7f1d1d' },  // 红色
    { foreColor: '#a78bfa', backColor: '#4c1d95' },  // 紫色
    { foreColor: '#ec4899', backColor: '#831843' },  // 粉色
    { foreColor: '#14b8a6', backColor: '#134e4a' },  // 青色
    { foreColor: '#f97316', backColor: '#7c2d12' },  // 深橙
];

// 4x4 固定颜色选择器（用于文字色和背景色）
const fixedColors = [
    // 第一行 - 浅色系
    '#ffffff', '#e0e0e0', '#ffcdd2', '#f8bbd0',
    // 第二行 - 亮色系
    '#60a5fa', '#10b981', '#f59e0b', '#ef4444',
    // 第三行 - 深色系
    '#1e3a5f', '#064e3b', '#78350f', '#7f1d1d',
    // 第四行 - 其他颜色
    '#a78bfa', '#ec4899', '#14b8a6', '#f97316'
];

// Load settings from localStorage
function loadSettings() {
    const saved = localStorage.getItem('uiInspectorSettings');
    if (saved) {
        try {
            searchSettings = JSON.parse(saved);
        } catch (e) {
            console.error('Failed to load settings:', e);
        }
    }
}

// Save settings to localStorage
function saveSettings() {
    localStorage.setItem('uiInspectorSettings', JSON.stringify(searchSettings));
}

// Modal functions
function showDeviceModal() {
    const modal = document.getElementById('deviceModal');
    modal.classList.add('show');
    updateDeviceModalList();
}

function closeDeviceModal(event) {
    if (event && event.target !== event.currentTarget) return;
    const modal = document.getElementById('deviceModal');
    modal.classList.remove('show');
}

function showDisplayModal() {
    const modal = document.getElementById('displayModal');
    modal.classList.add('show');
    updateDisplayModalList();
}

function closeDisplayModal(event) {
    if (event && event.target !== event.currentTarget) return;
    const modal = document.getElementById('displayModal');
    modal.classList.remove('show');
}

function showSettingsModal() {
    const modal = document.getElementById('settingsModal');
    modal.classList.add('show');
    
    // Populate settings - 只更新模式列表和 ignoreCase
    updatePatternList();
    document.getElementById('ignoreCase').checked = searchSettings.ignoreCase;
}

function showHelpModal() {
    const modal = document.getElementById('helpModal');
    if (modal) modal.classList.add('show');
}

function closeHelpModal(event) {
    if (event && event.target !== event.currentTarget) return;
    const modal = document.getElementById('helpModal');
    if (modal) modal.classList.remove('show');
}

function copyCommand(elementId) {
    const el = document.getElementById(elementId);
    if (!el) return;
    const text = (el.innerText || el.textContent || '').trim();
    if (!text) return;

    const onSuccess = () => {
        addLogEntry(`📋 已复制命令: ${text}`, 'success');
        showToast();
    };

    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(onSuccess).catch(() => {
            // fallback below
            try {
                const ta = document.createElement('textarea');
                ta.value = text;
                ta.style.position = 'fixed';
                ta.style.left = '-9999px';
                document.body.appendChild(ta);
                ta.select();
                document.execCommand('copy');
                ta.remove();
                onSuccess();
            } catch (e) {
                addLogEntry(`⚠️ 复制失败，请手动复制: ${text}`, 'warning');
                showToast();
            }
        });
    } else {
        try {
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.left = '-9999px';
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            ta.remove();
            onSuccess();
        } catch (e) {
            addLogEntry(`⚠️ 复制失败，请手动复制: ${text}`, 'warning');
            showToast();
        }
    }
}

function dismissQuickStartBanner() {
    const banner = document.getElementById('quickStartBanner');
    if (banner) {
        banner.classList.add('hidden');
    }
    try {
        localStorage.setItem('uiInspectorQuickStartDismissed', '1');
    } catch (_) {}
}

function initQuickStartBanner() {
    const banner = document.getElementById('quickStartBanner');
    if (!banner) return;
    try {
        const dismissed = localStorage.getItem('uiInspectorQuickStartDismissed') === '1';
        if (dismissed) {
            banner.classList.add('hidden');
        }
    } catch (_) {}
}

// 模式列表管理函数 - 每个关键字独立配色
function addPattern() {
    const input = document.getElementById('newPattern');
    const text = input.value.trim();
    
    if (!text) {
        alert('请输入搜索关键词！');
        return;
    }
    
    // 检查是否已存在
    if (searchSettings.patterns.some(p => p.text === text)) {
        alert('该搜索关键字已存在！');
        return;
    }
    
    // 自动分配颜色（循环使用预设颜色）
    const colorIndex = searchSettings.patterns.length % colorPresets.length;
    const colors = colorPresets[colorIndex];
    
    // 添加新的pattern对象
    searchSettings.patterns.push({
        text: text,
        foreColor: colors.foreColor,
        backColor: colors.backColor
    });
    
    input.value = '';
    updatePatternList();
    saveSettings();
}

function removePattern(index) {
    if (index >= 0 && index < searchSettings.patterns.length) {
        searchSettings.patterns.splice(index, 1);
        updatePatternList();
        saveSettings();
    }
}

function updatePatternColor(index, colorType, value) {
    if (index >= 0 && index < searchSettings.patterns.length) {
        searchSettings.patterns[index][colorType] = value;
        saveSettings();
    }
}

function updatePatternList() {
    const listContainer = document.getElementById('patternList');
    
    if (searchSettings.patterns.length === 0) {
        listContainer.innerHTML = '<div class="empty-state" style="padding: 15px; font-size: 13px; color: #9ca3af;">暂无搜索关键字<br><small style="font-size: 11px;">添加关键字后，可为每个关键字设置独立的高亮颜色</small></div>';
        return;
    }
    
    listContainer.innerHTML = '';
    searchSettings.patterns.forEach((pattern, index) => {
        const item = document.createElement('div');
        item.className = 'pattern-item-row';
        
        // 关键字文本
        const textSpan = document.createElement('span');
        textSpan.className = 'pattern-text';
        textSpan.innerText = pattern.text;
        textSpan.style.color = pattern.foreColor;
        textSpan.style.backgroundColor = pattern.backColor;
        item.appendChild(textSpan);
        
        // 颜色选择器容器
        const colorsDiv = document.createElement('div');
        colorsDiv.className = 'pattern-colors';
        
        // 文字色选择器按钮
        const foreColorBtn = document.createElement('button');
        foreColorBtn.className = 'color-picker-btn';
        foreColorBtn.style.backgroundColor = pattern.foreColor;
        foreColorBtn.title = '文字颜色';
        foreColorBtn.onclick = (e) => {
            e.stopPropagation();
            showColorPicker(index, 'foreColor', pattern.foreColor, foreColorBtn, textSpan);
        };
        colorsDiv.appendChild(foreColorBtn);
        
        // 背景色选择器按钮
        const backColorBtn = document.createElement('button');
        backColorBtn.className = 'color-picker-btn';
        backColorBtn.style.backgroundColor = pattern.backColor;
        backColorBtn.title = '背景颜色';
        backColorBtn.onclick = (e) => {
            e.stopPropagation();
            showColorPicker(index, 'backColor', pattern.backColor, backColorBtn, textSpan);
        };
        colorsDiv.appendChild(backColorBtn);
        
        item.appendChild(colorsDiv);
        
        // 删除按钮
        const removeBtn = document.createElement('button');
        removeBtn.className = 'pattern-remove-btn';
        removeBtn.innerText = '×';
        removeBtn.title = '删除此关键字';
        removeBtn.onclick = () => removePattern(index);
        item.appendChild(removeBtn);
        
        listContainer.appendChild(item);
    });
}

// 显示颜色选择器弹窗
function showColorPicker(patternIndex, colorType, currentColor, targetBtn, textSpan) {
    // 移除已存在的选择器
    const existing = document.querySelector('.color-picker-popup');
    if (existing) existing.remove();
    
    // 创建弹窗
    const popup = document.createElement('div');
    popup.className = 'color-picker-popup';
    
    // 创建4x4颜色网格
    const grid = document.createElement('div');
    grid.className = 'color-grid';
    
    fixedColors.forEach(color => {
        const colorBox = document.createElement('div');
        colorBox.className = 'color-box';
        colorBox.style.backgroundColor = color;
        if (color.toLowerCase() === currentColor.toLowerCase()) {
            colorBox.classList.add('selected');
        }
        colorBox.onclick = () => {
            updatePatternColor(patternIndex, colorType, color);
            targetBtn.style.backgroundColor = color;
            if (colorType === 'foreColor') {
                textSpan.style.color = color;
            } else {
                textSpan.style.backgroundColor = color;
            }
            popup.remove();
        };
        grid.appendChild(colorBox);
    });
    
    popup.appendChild(grid);
    
    // 定位弹窗
    const rect = targetBtn.getBoundingClientRect();
    popup.style.position = 'fixed';
    popup.style.left = rect.left + 'px';
    popup.style.top = (rect.bottom + 5) + 'px';
    
    document.body.appendChild(popup);
    
    // 点击外部关闭
    const closePopup = (e) => {
        if (!popup.contains(e.target) && e.target !== targetBtn) {
            popup.remove();
            document.removeEventListener('click', closePopup);
        }
    };
    setTimeout(() => {
        document.addEventListener('click', closePopup);
    }, 0);
}

function closeSettingsModal(event) {
    if (event && event.target !== event.currentTarget) return;
    const modal = document.getElementById('settingsModal');
    modal.classList.remove('show');
}

function applySettings() {
    // 只保存 ignoreCase，每个关键字都有独立的颜色配置
    searchSettings.ignoreCase = document.getElementById('ignoreCase').checked;
    
    saveSettings();
    closeSettingsModal();
    
    // Refresh tree view to apply new settings
    if (rootNode) {
        const treeList = document.createElement('div');
        traverseAndBuildTree(rootNode, treeList);
        treeContainer.innerHTML = '';
        treeContainer.appendChild(treeList);
    }
    
    // Refresh properties view if node is selected
    if (selectedNode) {
        const attrs = getAttributes(selectedNode);
        renderProperties(attrs);
    }
}

function resetSettings() {
    // 重置为默认设置 - 只保留 patterns 数组和 ignoreCase
    searchSettings = {
        patterns: [],
        ignoreCase: true
    };
    saveSettings();
    showSettingsModal(); // Refresh the modal with default values
}

function updateDeviceModalList() {
    const listContainer = document.getElementById('deviceModalList');
    if (devicesList.length === 0) {
        listContainer.innerHTML = '<div class="empty-state">未发现设备</div>';
        return;
    }
    
    listContainer.innerHTML = '';
    devicesList.forEach(d => {
        const item = document.createElement('div');
        item.className = 'modal-item';
        if (currentDevice && currentDevice.serial === d.serial) {
            item.classList.add('selected');
        }
        
        const icon = document.createElement('span');
        icon.className = 'modal-item-icon';
        icon.innerText = '📱';
        item.appendChild(icon);
        
        const text = document.createElement('div');
        text.className = 'modal-item-text';
        const ssLabel = d.ss_type ? ` [${d.ss_type}]` : '';
        text.innerHTML = `<strong>${d.model}</strong><br><small style="color: #6b7280">${d.serial}${ssLabel}</small>`;
        item.appendChild(text);
        
        // 是否需要初始化（后端负责判断；避免前端用 serial 硬编码误判）
        const isUnconnectedSS4 = !!d.needs_init;
        
        if (isUnconnectedSS4) {
            // 未连接标识
            const unconnectedBadge = document.createElement('span');
            unconnectedBadge.className = 'modal-item-badge';
            unconnectedBadge.style.backgroundColor = '#ef4444';
            unconnectedBadge.innerText = '未连接';
            unconnectedBadge.title = '需要执行初始化连接';
            item.appendChild(unconnectedBadge);
            
            // 连接按钮
            const connectBtn = document.createElement('button');
            connectBtn.className = 'btn-connect';
            connectBtn.innerText = '连接';
            connectBtn.title = '初始化SS4设备连接';
            connectBtn.onclick = async (e) => {
                e.stopPropagation(); // 阻止触发设备选择
                await initSS4Device(d);
            };
            item.appendChild(connectBtn);
        } else if (d.ss_type) {
            // 已连接的SS设备显示类型badge
            const badge = document.createElement('span');
            badge.className = 'modal-item-badge';
            badge.innerText = d.ss_type;
            item.appendChild(badge);
        }
        
        // 只有非未连接SS4设备才能直接点击选择
        if (!isUnconnectedSS4) {
            item.onclick = () => {
                selectDevice(d);
                closeDeviceModal();
            };
        } else {
            // 未连接SS4设备点击时提示需要先连接
            item.onclick = () => {
                alert('⚠️ 此SS4设备尚未连接\n\n请点击"连接"按钮进行初始化连接');
            };
            item.style.cursor = 'default';
        }
        
        listContainer.appendChild(item);
    });
}

// SS4设备初始化函数 - 改进版：在弹窗中显示进度
async function initSS4Device(device) {
    const serial = device.serial;
    console.log(`[InitSS4Device] 开始初始化SS4设备: ${serial}`);
    
    // 找到对应的modal-item和连接按钮
    const modalList = document.getElementById('deviceModalList');
    const items = modalList.querySelectorAll('.modal-item');
    let targetItem = null;
    let connectBtn = null;
    
    // 找到对应设备的项
    items.forEach(item => {
        const textContent = item.textContent || '';
        if (textContent.includes(serial)) {
            targetItem = item;
            connectBtn = item.querySelector('.btn-connect');
        }
    });
    
    // 显示连接日志Toast
    clearLog();
    showToast();
    addLogEntry(`🚀 开始初始化SS4设备: ${serial}`, 'info');
    
    // 在弹窗项中显示进度状态
    if (connectBtn) {
        connectBtn.disabled = true;
        connectBtn.textContent = '连接中...';
        connectBtn.style.background = '#f59e0b';
    }
    
    // 添加进度指示器到设备项
    let progressIndicator = null;
    if (targetItem) {
        progressIndicator = document.createElement('div');
        progressIndicator.className = 'init-progress';
        progressIndicator.innerHTML = '<div class="progress-spinner"></div><span class="progress-text">正在初始化...</span>';
        targetItem.appendChild(progressIndicator);
        targetItem.style.pointerEvents = 'none'; // 防止点击
    }
    
    const updateProgress = (text) => {
        if (progressIndicator) {
            const textEl = progressIndicator.querySelector('.progress-text');
            if (textEl) textEl.textContent = text;
        }
    };
    
    try {
        updateProgress('步骤1/5: adb root');
        addLogEntry(`📝 步骤1: 执行 adb root`, 'info');
        await new Promise(r => setTimeout(r, 200)); // 让用户看到进度
        
        updateProgress('步骤2/5: adbconnect.sh');
        addLogEntry(`📝 步骤2: 执行 adb shell adbconnect.sh`, 'info');
        await new Promise(r => setTimeout(r, 200));
        
        updateProgress('步骤3/5: adb forward');
        addLogEntry(`📝 步骤3: 执行 adb forward tcp:5559 tcp:5557`, 'info');
        await new Promise(r => setTimeout(r, 200));
        
        updateProgress('步骤4/5: adb connect');
        addLogEntry(`📝 步骤4: 执行 adb connect localhost:5559`, 'info');
        await new Promise(r => setTimeout(r, 200));
        
        updateProgress('步骤5/5: 最终root');
        addLogEntry(`📝 步骤5: 执行 adb -s localhost:5559 root`, 'info');
        
        const response = await fetch('/api/init-ss4', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ serial: serial })
        });
        
        if (!response.ok) {
            const errorText = await response.text();
            console.error(`[InitSS4Device] 初始化失败: ${errorText}`);
            addLogEntry(`❌ SS4初始化失败: ${errorText}`, 'error');
            
            // 恢复按钮状态
            if (connectBtn) {
                connectBtn.disabled = false;
                connectBtn.textContent = '连接';
                connectBtn.style.background = '';
            }
            if (progressIndicator) progressIndicator.remove();
            if (targetItem) targetItem.style.pointerEvents = '';
            
            alert(`❌ SS4设备初始化失败:\n\n${errorText}`);
            return;
        }
        
        const data = await response.json();
        console.log(`[InitSS4Device] 初始化成功:`, data);
        
        updateProgress('✅ 初始化成功');
        addLogEntry(`✅ SS4初始化成功！`, 'success');
        addLogEntry(`🔄 新设备地址: ${data.new_serial}`, 'success');
        
        // 等待连接稳定
        updateProgress('⏳ 等待连接稳定...');
        addLogEntry(`⏳ 等待连接稳定...`, 'info');
        await new Promise(resolve => setTimeout(resolve, 1000));
        
        // 刷新设备列表
        updateProgress('🔄 刷新设备列表...');
        addLogEntry(`🔄 刷新设备列表...`, 'info');
        await refreshDeviceList(false);
        
        // 更新设备选择弹窗 - 这会重新渲染整个列表
        updateProgress('🎉 完成！');
        addLogEntry(`🎉 SS4设备已就绪，请选择设备并连接`, 'success');
        
        // 短暂延迟后刷新弹窗显示
        await new Promise(r => setTimeout(r, 500));
        updateDeviceModalList();
        
        // 自动选中新连接的设备（localhost:5559）
        const newDevice = devicesList.find(d => d.serial === data.new_serial);
        if (newDevice) {
            console.log('[InitSS4Device] 自动选中新设备:', data.new_serial);
            selectDevice(newDevice);
            
            // 在弹窗中高亮显示
            setTimeout(() => {
                const items = document.querySelectorAll('.modal-item');
                items.forEach(item => {
                    if (item.textContent.includes(data.new_serial)) {
                        item.classList.add('selected');
                        // 添加闪烁效果提示用户
                        item.style.animation = 'pulse 0.5s ease-in-out 3';
                    }
                });
            }, 100);
        }
        
    } catch (e) {
        console.error(`[InitSS4Device] 初始化异常:`, e);
        addLogEntry(`❌ 初始化异常: ${e.message}`, 'error');
        
        // 恢复按钮状态
        if (connectBtn) {
            connectBtn.disabled = false;
            connectBtn.textContent = '连接';
            connectBtn.style.background = '';
        }
        if (progressIndicator) progressIndicator.remove();
        if (targetItem) targetItem.style.pointerEvents = '';
        
        alert(`❌ SS4设备初始化失败:\n\n${e.message}`);
    }
}

function updateDisplayModalList() {
    const listContainer = document.getElementById('displayModalList');
    if (displaysList.length === 0) {
        listContainer.innerHTML = '<div class="empty-state">未发现显示屏幕</div>';
        return;
    }
    
    listContainer.innerHTML = '';
    displaysList.forEach(d => {
        const item = document.createElement('div');
        item.className = 'modal-item';
        if (currentDisplay === d.id) {
            item.classList.add('selected');
        }
        
        const icon = document.createElement('span');
        icon.className = 'modal-item-icon';
        icon.innerText = '🖥️';
        item.appendChild(icon);
        
        const text = document.createElement('div');
        text.className = 'modal-item-text';
        text.innerText = d.description;
        item.appendChild(text);
        
        item.onclick = () => {
            selectDisplay(d.id, d.description);
            closeDisplayModal();
        };
        
        listContainer.appendChild(item);
    });
}

function selectDevice(device) {
    currentDevice = device;
    const btn = document.getElementById('deviceSelectText');
    // 显示设备型号，如果是SS设备则显示SS类型，否则显示model
    const displayName = device.ss_type || device.model;
    btn.innerText = displayName;
    
    // Add subtle animation
    btn.style.transform = 'scale(0.98)';
    setTimeout(() => {
        btn.style.transform = 'scale(1)';
    }, 100);
    
    // 启用display选择器
    enableDisplaySelector();
    
    onDeviceChanged();
}

function selectDisplay(displayId, description) {
    currentDisplay = displayId;
    const btn = document.getElementById('displaySelectText');
    btn.innerText = description;
    
    // Add subtle animation
    btn.style.transform = 'scale(0.98)';
    setTimeout(() => {
        btn.style.transform = 'scale(1)';
    }, 100);
    
    // 只是选择display，不自动连接或刷新
    console.log("[SelectDisplay] 已选择显示屏幕:", displayId, description, "- 需要点击'连接设备'按钮才会连接");
}

// 启用/禁用display选择器
function enableDisplaySelector() {
    const displayBtn = document.getElementById('displaySelectBtn');
    const displayRefreshBtn = displayBtn.nextElementSibling; // 刷新按钮
    
    if (displayBtn) {
        displayBtn.disabled = false;
        displayBtn.style.opacity = '1';
        displayBtn.style.cursor = 'pointer';
        displayBtn.title = '选择显示屏幕';
    }
    
    if (displayRefreshBtn) {
        displayRefreshBtn.disabled = false;
        displayRefreshBtn.style.opacity = '1';
        displayRefreshBtn.style.cursor = 'pointer';
    }
    
    console.log('[DisplaySelector] ✅ Display选择器已启用');
}

function disableDisplaySelector() {
    const displayBtn = document.getElementById('displaySelectBtn');
    const displayText = document.getElementById('displaySelectText');
    const displayRefreshBtn = displayBtn.nextElementSibling; // 刷新按钮
    
    if (displayBtn) {
        displayBtn.disabled = true;
        displayBtn.style.opacity = '0.5';
        displayBtn.style.cursor = 'not-allowed';
        displayBtn.title = '请先选择设备';
    }
    
    if (displayText) {
        displayText.innerText = '请先选择设备';
    }
    
    if (displayRefreshBtn) {
        displayRefreshBtn.disabled = true;
        displayRefreshBtn.style.opacity = '0.5';
        displayRefreshBtn.style.cursor = 'not-allowed';
    }
    
    console.log('[DisplaySelector] 🔒 Display选择器已禁用');
}

// Init
window.onload = () => {
    loadSettings(); // Load settings from localStorage
    initQuickStartBanner();
    disableDisplaySelector(); // 初始化时禁用display选择器
    refreshDeviceList(); // 只加载设备列表，不自动连接
    
    // 监听数据源开关变化 - 只更新标签，不立即启用/禁用服务
    const dataSourceSwitch = document.getElementById('useAccessibilityService');
    const dataSourceLabel = document.getElementById('dataSourceLabel');

    // 默认策略：如果 index.html 把开关设为 checked，则这里同步 label（防止 JCEF/缓存导致 label 不一致）
    if (dataSourceSwitch && dataSourceLabel) {
        dataSourceLabel.textContent = dataSourceSwitch.checked ? '辅助服务' : 'UIAutomator';

        dataSourceSwitch.addEventListener('change', function() {
            if (this.checked) {
                dataSourceLabel.textContent = '辅助服务';
                console.log('[DataSource] 已选择辅助服务模式（将在连接设备时生效）');
            } else {
                dataSourceLabel.textContent = 'UIAutomator';
                console.log('[DataSource] 已选择UIAutomator模式');
            }

            // 如果已连接设备，刷新hierarchy以使用新的数据源
            if (rootNode) {
                console.log('[DataSource] ⚠️ 数据源已更改，刷新后将使用新的数据源');
                refreshHierarchy();
            }
        });
    }
};

// Toast notification helpers
let toastTimeout = null;

function showToast() {
    const toast = document.getElementById('connectionToast');
    toast.classList.add('show');
    
    // Clear existing timeout
    if (toastTimeout) {
        clearTimeout(toastTimeout);
    }
    
    // Auto close after 5 seconds
    toastTimeout = setTimeout(() => {
        closeToast();
    }, 5000);
}

function closeToast() {
    const toast = document.getElementById('connectionToast');
    toast.classList.remove('show');
    if (toastTimeout) {
        clearTimeout(toastTimeout);
        toastTimeout = null;
    }
}

function addLogEntry(message, type = 'info') {
    const logContainer = document.getElementById('toastLog');
    const timestamp = new Date().toLocaleTimeString();
    const colors = {
        'info': '#3b82f6',
        'success': '#10b981',
        'warning': '#f59e0b',
        'error': '#ef4444'
    };
    const color = colors[type] || colors['info'];
    
    const entry = document.createElement('div');
    entry.style.marginBottom = '8px';
    entry.style.paddingLeft = '10px';
    entry.style.borderLeft = `3px solid ${color}`;
    entry.innerHTML = `<span style="color: #6b7280; font-size: 11px;">${timestamp}</span><br><span style="color: ${color}; font-weight: 500;">${message}</span>`;
    
    logContainer.appendChild(entry);
    logContainer.scrollTop = logContainer.scrollHeight;
}

function clearLog() {
    const logContainer = document.getElementById('toastLog');
    logContainer.innerHTML = '';
}

// ---- networking helpers ----
// fetch with timeout to avoid "forever pending" in JCEF
async function fetchWithTimeout(url, options = {}, timeoutMs = 8000) {
    const controller = new AbortController();
    const id = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const res = await fetch(url, {
            ...options,
            signal: controller.signal
        });
        return res;
    } finally {
        clearTimeout(id);
    }
}

function formatMs(ms) {
    if (ms == null || Number.isNaN(ms)) return '';
    return `${Math.round(ms)}ms`;
}

function runInBackground(label, fn) {
    Promise.resolve()
        .then(fn)
        .catch(e => {
            console.warn(`[BG][${label}] failed`, e);
            addLogEntry(`⚠️ ${label}失败: ${e.message || e}`, 'warning');
        });
}

async function refreshDeviceList(autoConnect = false) {
    console.log("[RefreshDeviceList] 开始获取设备列表... autoConnect:", autoConnect);
    const btn = document.getElementById('deviceSelectText');
    
    // 如果已经有设备，不要覆盖按钮文本
    if (!currentDevice) {
        btn.innerText = '正在获取设备...';
    }
    
    try {
        const res = await fetch('/api/devices');
        devicesList = await res.json();
        console.log("[RefreshDeviceList] 获取到设备:", devicesList);

        if (devicesList.length === 0) {
            console.log("[RefreshDeviceList] 没有发现设备");
            btn.innerText = '未发现设备';
            return;
        }

        // 如果只有一个设备，自动选择；如果有多个设备，不自动选择
        if (devicesList.length === 1 && !currentDevice) {
            currentDevice = devicesList[0];
            const displayName = currentDevice.ss_type || currentDevice.model;
            btn.innerText = displayName;
            console.log("[RefreshDeviceList] 只有一个设备，自动选择:", currentDevice.serial);
            
            // 只获取显示列表，不连接
            onDeviceChanged();
        } else if (devicesList.length > 1 && !currentDevice) {
            // 多个设备时，不自动选择
            console.log("[RefreshDeviceList] 检测到多个设备，请手动选择");
            btn.innerText = `请选择设备 (${devicesList.length}个)`;
        }
        
        // 仅在明确请求自动连接时才连接（点击刷新按钮时）
        if (autoConnect) {
            console.log(`[AutoConnect] 用户请求自动连接到: ${currentDevice.serial}`);
            
            clearLog();
            showToast();
            
            const statusEl = document.getElementById('status');
            statusEl.innerText = '正在连接...';
            statusEl.style.color = '#f59e0b';
            
            addLogEntry(`🔍 检测到设备: ${currentDevice.model}`, 'info');
            addLogEntry(`📱 Serial: ${currentDevice.serial}`, 'info');
            if (currentDevice.ss_type) {
                addLogEntry(`⚙️ 设备类型: ${currentDevice.ss_type} (需要初始化)`, 'warning');
            } else {
                addLogEntry(`✅ 普通Android设备`, 'info');
            }
            
            setTimeout(() => connectDevice(), 500);
        }
    } catch (e) {
        console.error("[RefreshDeviceList] 错误:", e);
        const statusEl = document.getElementById('status');
        statusEl.innerText = `获取设备失败: ${e.message}`;
        statusEl.style.color = '#ef4444';
        btn.innerText = '获取设备失败';
        
        if (autoConnect) {
            clearLog();
            showToast();
            addLogEntry(`❌ 获取设备失败: ${e.message}`, 'error');
        }
    }
}

async function onDeviceChanged() {
    if (!currentDevice) return;
    console.log("Device changed to:", currentDevice.serial);
    refreshDisplayList();
}

async function refreshDisplayList(keepCurrentSelection = false) {
    const btn = document.getElementById('displaySelectText');
    const previousDisplay = currentDisplay; // 保存用户当前选择的display
    btn.innerText = '正在获取屏幕...';
    
    try {
        if (!currentDevice) return;
        console.log("Fetching displays for:", currentDevice.serial);
        const res = await fetch(`/api/displays?serial=${currentDevice.serial}`);
        displaysList = await res.json();
        console.log("Displays received:", displaysList);
        
        if (displaysList.length > 0) {
            // 如果需要保持当前选择，且当前选择的display还在列表中，就保持不变
            if (keepCurrentSelection && previousDisplay) {
                const displayExists = displaysList.some(d => d.id === previousDisplay);
                if (displayExists) {
                    // 用户选择的display还在列表中，保持选择
                    console.log("[RefreshDisplayList] 保持用户选择的display:", previousDisplay);
                    const displayInfo = displaysList.find(d => d.id === previousDisplay);
                    if (displayInfo) {
                        selectDisplay(displayInfo.id, displayInfo.description);
                    }
                    return;
                }
            }
            
            // 否则选择第一个display（初次加载或用户选择的display不存在了）
            currentDisplay = displaysList[0].id;
            selectDisplay(displaysList[0].id, displaysList[0].description);
        }
    } catch (e) {
        console.error("Failed to get displays", e);
        btn.innerText = '默认屏幕 (0)';
        displaysList = [{ id: "0", description: "默认屏幕 (0)" }];
    }
}

async function connectDevice() {
    if (!currentDevice) {
        console.error("[ConnectDevice] 没有选择有效设备");
        alert("请先选择一个有效的设备！");
        return;
    }
    
    const serial = currentDevice.serial;
    const needsInit = currentDevice.needs_init || false;
    const ssType = currentDevice.ss_type || 'SS';
    
    console.log(`[ConnectDevice] 开始连接设备: ${serial}`);
    console.log(`[ConnectDevice] 设备信息 - Serial: ${serial}, SS类型: ${ssType}, 需要初始化: ${needsInit}`);
    
    // 显示连接日志Toast
    clearLog();
    showToast();
    addLogEntry(`🚀 开始连接设备: ${serial}`, 'info');

    loading.classList.remove('hidden');
    
    try {
        let targetSerial = serial;
        
        // Step 1: Auto initialize SS device if needed
        if (needsInit) {
            console.log(`[ConnectDevice] 检测到${ssType}设备，开始初始化...`);
            addLogEntry(`⚙️ 检测到${ssType}设备，需要执行初始化命令`, 'warning');
            
            const statusEl = document.getElementById('status');
            statusEl.innerText = `正在初始化${ssType}设备...`;
            statusEl.style.color = '#f59e0b';
            
            addLogEntry(`📝 步骤1: 执行 adb root`, 'info');
            addLogEntry(`📝 步骤2: 执行 adb shell adbconnect.sh`, 'info');
            addLogEntry(`📝 步骤3: 执行 adb forward tcp:5559 tcp:5557`, 'info');
            addLogEntry(`📝 步骤4: 执行 adb connect localhost:5559`, 'info');
            addLogEntry(`📝 步骤5: 执行 adb -s localhost:5559 root`, 'info');
            
            console.log(`[ConnectDevice] 调用 /api/init-ss4 API`);
            const initRes = await fetch('/api/init-ss4', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ serial: serial })
            });
            
            console.log(`[ConnectDevice] 初始化API响应状态: ${initRes.status}`);
            
            if (!initRes.ok) {
                const errorText = await initRes.text();
                console.error(`[ConnectDevice] 初始化失败: ${errorText}`);
                addLogEntry(`❌ ${ssType}初始化失败: ${errorText}`, 'error');
                throw new Error(`${ssType}初始化失败: ${errorText}`);
            }
            
            const initData = await initRes.json();
            console.log(`[ConnectDevice] ${ssType}初始化成功:`, initData);
            targetSerial = initData.new_serial; // Use localhost:5559
            console.log(`[ConnectDevice] 新的serial: ${targetSerial}`);
            
            addLogEntry(`✅ ${ssType}初始化成功！`, 'success');
            addLogEntry(`🔄 新设备地址: ${targetSerial}`, 'success');
            
            statusEl.innerText = `${ssType}初始化完成，正在连接...`;
            
            // Wait a bit for the connection to stabilize
            console.log("[ConnectDevice] 等待连接稳定...");
            addLogEntry(`⏳ 等待连接稳定...`, 'info');
            await new Promise(resolve => setTimeout(resolve, 1000));
            
            // Refresh device list to include localhost:5559
            console.log("[ConnectDevice] 刷新设备列表...");
            await refreshDeviceList(false); // false = don't auto-connect
            
            // Update currentDevice to the new localhost:5559 for SS4
            currentDevice = {
                ...currentDevice,
                serial: targetSerial
            };
            
            console.log(`[ConnectDevice] 已切换到新serial: ${targetSerial}`);
        } else {
            console.log("[ConnectDevice] 普通设备，无需初始化");
            addLogEntry(`✅ 普通Android设备，直接连接`, 'info');
        }
        
        // Step 2: Connect to the device FIRST (fast path)
        console.log(`[ConnectDevice] 连接到设备: ${targetSerial}`);
        addLogEntry(`🔌 正在建立连接...`, 'info');

        const tConnectStart = performance.now();
        const res = await fetchWithTimeout('/api/connect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ serial: targetSerial })
        }, 8000);
        console.log(`[ConnectDevice] 连接API响应状态: ${res.status}`);
        
        if (!res.ok) {
            const errorText = await res.text();
            console.error(`[ConnectDevice] 连接失败: ${errorText}`);
            addLogEntry(`❌ 连接失败: ${errorText}`, 'error');
            throw new Error(errorText);
        }
        
        const data = await res.json();
        addLogEntry(`⏱️ 连接耗时: ${formatMs(performance.now() - tConnectStart)}`, 'info');
        console.log("[ConnectDevice] 连接成功，设备信息:", data);
        const productName = data.info.productName || "Unknown Device";
        const statusEl = document.getElementById('status');
        statusBaseText = `已连接: ${productName}`;
        // reset tags on new connection
        statusTags.clear();
        renderStatus();
        statusEl.classList.remove('status-badge');
        statusEl.style.color = '#10b981';
        statusEl.style.fontWeight = 'bold';
        
        addLogEntry(`✅ 连接成功: ${productName}`, 'success');

        // Step 3: Refresh display list in BACKGROUND (do not block connection)
        runInBackground('获取显示屏幕', async () => {
            console.log("[ConnectDevice] (BG) 刷新显示列表...");
            addLogEntry(`🖥️ 检测显示屏幕...`, 'info');
            // keep user selection; if it takes too long, user can still work with default display
            await refreshDisplayList(true);
        });

        // Step 3.5: Ensure accessibility APK in BACKGROUND (avoid blocking connection)
        runInBackground('自动安装无障碍APK', async () => {
            const tStart = performance.now();
            addLogEntry('📦 检查/安装 CarUI 无障碍APK（自动）...', 'info');
            const ensureApkRes = await fetchWithTimeout('/api/accessibility/ensure', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    serial: targetSerial,
                    install_if_missing: true,
                    enable_service: false,
                    probe_running: false
                })
            }, 60000);
            if (ensureApkRes.ok) {
                const ensureApkData = await ensureApkRes.json();
                if (ensureApkData && Array.isArray(ensureApkData.steps)) {
                    ensureApkData.steps.forEach(s => addLogEntry(`📦 ${s}`, 'info'));
                }
                addLogEntry(`⏱️ APK检查/安装耗时: ${formatMs(performance.now() - tStart)}`, 'info');
                if (ensureApkData.apk_installed) {
                    addLogEntry('✅ 无障碍APK已就绪（已安装或已自动安装）', 'success');
                } else {
                    addLogEntry('⚠️ 无障碍APK未安装（可能是 adb/权限/serial 问题）', 'warning');
                    // APK 未安装 → 弹出手动安装引导弹窗
                    showApkInstallModal();
                }
            } else {
                const errText = await ensureApkRes.text();
                addLogEntry(`⚠️ 自动安装APK失败: ${errText}`, 'warning');
            }
        });

        // Step 4: If user selected accessibility mode, prefer "status check" first.
        // If already running, don't touch secure settings (fits your "我肯定会打开" usage).
        const useAccessibility = document.getElementById('useAccessibilityService')?.checked;
        if (useAccessibility) {
            try {
                const st = await fetchAccessibilityStatus();
                if (st && st.running) {
                    addLogEntry('✅ 辅助服务已在运行（跳过一键启用）', 'success');
                } else {
                    addLogEntry('♿ 一键启动辅助服务（安装/启用/校验）...', 'info');
                    const ensureRes = await fetchWithTimeout('/api/accessibility/ensure', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            serial: targetSerial,
                            install_if_missing: true,
                            enable_service: true,
                            probe_running: true
                        })
                    }, 60000);

                    if (ensureRes.ok) {
                        const ensureData = await ensureRes.json();
                        // Print steps to toast
                        if (ensureData && Array.isArray(ensureData.steps)) {
                            ensureData.steps.forEach(s => addLogEntry(`♿ ${s}`, 'info'));
                        }

                        if (ensureData.running) {
                            addLogEntry('✅ 辅助服务已运行并通过校验 (/api/status)', 'success');
                        } else {
                            addLogEntry('⚠️ 辅助服务未能自动拉起（可能需要ROOT/系统权限或手动在设置里开启）', 'warning');
                        }
                    } else {
                        const errText = await ensureRes.text();
                        addLogEntry(`⚠️ 一键启动辅助服务失败: ${errText}`, 'warning');
                    }
                }
            } catch (e) {
                addLogEntry(`⚠️ 辅助服务检查/拉起异常: ${e.message}`, 'warning');
            }
        }

        // Step 4.5: Refresh accessibility status tag
        await updateAccessibilityUIStatus();
        
        console.log("[ConnectDevice] 开始刷新快照...");
        addLogEntry(`📸 正在获取屏幕截图...`, 'info');
        refreshSnapshot();
        
        addLogEntry(`🎉 全部完成！设备已就绪`, 'success');

    } catch (e) {
        console.error("[ConnectDevice] 连接过程出错:", e);
        const statusEl = document.getElementById('status');
        statusEl.innerText = `错误: ${e.message}`;
        statusEl.style.color = '#ef4444';
        addLogEntry(`❌ 连接失败: ${e.message}`, 'error');
        alert("连接失败: " + e.message);
    } finally {
        loading.classList.add('hidden');
    }
}

async function fetchAccessibilityStatus() {
    try {
        const res = await fetch('/api/accessibility/status', { cache: 'no-cache' });
        if (!res.ok) return null;
        return await res.json();
    } catch (e) {
        console.warn('[AccessibilityStatus] fetch failed', e);
        return null;
    }
}

function setStatusBadgeWithExtras(extraText) {
    // Backward compatible wrapper: treat extraText as a single tag, stored under 'extra'
    if (extraText) {
        statusTags.set('extra', extraText.replace(/^\[/, '').replace(/\]$/, ''));
    } else {
        statusTags.delete('extra');
    }
    renderStatus();
}

async function updateAccessibilityUIStatus() {
    const useAccessibility = document.getElementById('useAccessibilityService')?.checked;
    if (!useAccessibility) {
        // not in accessibility mode
        return;
    }

    const data = await fetchAccessibilityStatus();
    if (!data) {
        accessibilityStatus = { enabled: false, running: false, checkedAt: Date.now() };
        statusTags.set('a11y', '辅助服务:未知');
        renderStatus();
        addLogEntry('⚠️ 辅助服务状态获取失败（请确认已安装APK并已开启服务）', 'warning');
        return;
    }

    accessibilityStatus = { ...data, checkedAt: Date.now() };
    const enabledText = data.enabled ? '已启用' : '未启用';
    const runningText = data.running ? '运行中' : '未运行';
    statusTags.set('a11y', `辅助服务:${runningText}`);
    renderStatus();

    if (!data.enabled || !data.running) {
        addLogEntry(`⚠️ 辅助服务异常：${enabledText} / ${runningText}`, 'warning');
        addLogEntry('💡 建议：到系统"无障碍"里打开 CarUI Accessibility；或重启服务后重连', 'info');
        // 辅助服务未运行 → 弹出 APK 安装/引导弹窗
        showApkInstallModal();
    } else {
        addLogEntry('✅ 辅助服务运行正常', 'success');
    }
}

async function refreshSnapshot(forceShowLoading = true) {
    // 如果之前被“关闭截屏页面/重启清理”隐藏了，这里恢复显示
    const screenEmpty = document.getElementById('screenEmptyState');
    if (screenEmpty) screenEmpty.classList.add('hidden');

    // 给刷新按钮添加视觉反馈和马里奥金币动画
    const refreshBtn = document.getElementById('refreshSnapshotBtn');
    if (refreshBtn) {
        refreshBtn.classList.add('refreshing');
        refreshBtn.textContent = '? 刷新中...';
        
        // 创建金币弹出动画
        const coin = document.createElement('div');
        coin.className = 'coin-animation';
        coin.textContent = '🪙';
        refreshBtn.style.position = 'relative';
        refreshBtn.appendChild(coin);
        
        // 1.2秒后移除金币元素（与CSS动画时长一致）
        setTimeout(() => {
            if (coin.parentNode) {
                coin.remove();
            }
        }, 1200);
    }
    
    // 不显示loading蒙层，只用马里奥金币特效
    try {
        // Parallel refresh
        await Promise.all([refreshScreen(), refreshHierarchy()]);
    } finally {
        // 恢复刷新按钮状态
        if (refreshBtn) {
            refreshBtn.classList.remove('refreshing');
            refreshBtn.textContent = '📸 刷新';
        }
    }
}

function refreshScreen() {
    return new Promise((resolve) => {
        const displayId = currentDisplay || "0";
        const img = new Image();
        img.src = `/api/screenshot?display=${displayId}&t=${new Date().getTime()}`;
        img.onload = async () => {
            try {
                // 截图已恢复，隐藏“截屏已关闭”遮罩
                const screenEmpty = document.getElementById('screenEmptyState');
                if (screenEmpty) screenEmpty.classList.add('hidden');

                // 开启异步解码，避免主线程卡顿，实现 scrcpy 般的流畅感
                if (img.decode) await img.decode();
                screenImage = img;

                // Canvas内部尺寸直接使用设备分辨率，不需要2x缩放
                // 这样hierarchy的bounds坐标就能直接对应到Canvas坐标
                canvas.width = screenImage.naturalWidth;
                canvas.height = screenImage.naturalHeight;

                ctx.imageSmoothingEnabled = true;
                ctx.imageSmoothingQuality = 'high';
                drawScreen();

                // After drawing screenshot, detect black/secure-protected content
                await updateSecureWarningByScreenshot();
                resolve();
            } catch (err) {
                console.warn("Decode failed", err);
                resolve();
            }
        };
        img.onerror = () => {
            console.warn("无法获取截图");
            resolve();
        };
    });
}

function toggleSidebar() {
    const container = document.querySelector('.main-container');
    container.classList.toggle('sidebar-hidden');
    // Canvas should automatically adapt due to CSS, but we can force a redraw
    setTimeout(drawScreen, 350);
}

// 全局变量：存储最后点击的坐标
let lastClickX = null;
let lastClickY = null;
let clickCrosshairTimeout = null;

function drawScreen() {
    if (!screenImage.src) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // 渲染截图 (填满整个Canvas)
    ctx.drawImage(screenImage, 0, 0, canvas.width, canvas.height);

    // 绘制 UI 高亮
    // Canvas内部尺寸 = 设备分辨率，所以scale = 1，不需要缩放
    ctx.save();

    // Draw Hover
    if (hoverNode && hoverNode !== selectedNode) {
        drawHighlight(hoverNode, '#3b82f6', 'rgba(59, 130, 246, 0.1)', 1);
    }

    // Draw Selected
    if (selectedNode) {
        drawHighlight(selectedNode, '#ef4444', 'rgba(239, 68, 68, 0.2)', 1);
    }

    // 绘制点击位置的红色十字准星
    if (lastClickX !== null && lastClickY !== null) {
        drawClickCrosshair(lastClickX, lastClickY);
    }

    // 绘制 clickable=true 节点高亮边框
    const hlClickable = document.getElementById('highlightClickableBounds');
    if (hlClickable && hlClickable.checked && rootNode) {
        _highlightAllClickable(rootNode);
    }

    ctx.restore();
}

// --- Secure/black screenshot detection & UX warning ---
let lastScreenshotBlackRatio = null;
let lastSecureDiagnoseAt = 0;
let lastSecureDiagnose = null;

function showSecureWarning(messageHtml) {
    const el = document.getElementById('secureWarning');
    if (!el) return;
    el.innerHTML = `
      <div class="msg">${messageHtml}</div>
      <button class="close" onclick="document.getElementById('secureWarning').classList.add('hidden')">关闭</button>
    `;
    el.classList.remove('hidden');
}

function hideSecureWarning() {
    const el = document.getElementById('secureWarning');
    if (!el) return;
    el.classList.add('hidden');
}

function computeBlackRatioFromCanvas(sampleStep = 20) {
    // returns ratio of pixels that are near-black (0..1)
    try {
        const w = canvas.width;
        const h = canvas.height;
        if (!w || !h) return null;
        const imgData = ctx.getImageData(0, 0, w, h).data;
        let black = 0;
        let total = 0;
        // sample every N pixels to keep fast
        for (let y = 0; y < h; y += sampleStep) {
            for (let x = 0; x < w; x += sampleStep) {
                const idx = (y * w + x) * 4;
                const r = imgData[idx];
                const g = imgData[idx + 1];
                const b = imgData[idx + 2];
                // near black threshold
                if (r < 8 && g < 8 && b < 8) black++;
                total++;
            }
        }
        if (total === 0) return null;
        return black / total;
    } catch (e) {
        return null;
    }
}

async function diagnoseSecureIfNeeded() {
    const now = Date.now();
    if (now - lastSecureDiagnoseAt < 5000) return lastSecureDiagnose; // throttle
    lastSecureDiagnoseAt = now;
    try {
        const res = await fetch('/api/diagnose/secure', { cache: 'no-cache' });
        if (!res.ok) return null;
        lastSecureDiagnose = await res.json();
        return lastSecureDiagnose;
    } catch (e) {
        return null;
    }
}

async function updateSecureWarningByScreenshot() {
    // Decide based on black ratio.
    // Note: some UIs have large black background, so we only warn when ratio is extremely high.
    const ratio = computeBlackRatioFromCanvas(25);
    if (ratio == null) return;
    lastScreenshotBlackRatio = ratio;

    // If it's mostly black, we further diagnose.
    if (ratio >= 0.985) {
        const diag = await diagnoseSecureIfNeeded();
        if (diag && diag.has_secure_layer) {
            const top = (diag.resumed_activities || []).slice(-1)[0] || '';
            const layer = (diag.secure_layers || [])[0]?.layer || '';
            showSecureWarning(
                `⚠️ 检测到当前页面/窗口可能<strong>禁止截屏</strong>（SurfaceFlinger: <code>isSecure=true</code>）。` +
                `<br/>因此截图区域会显示为黑/空白，但辅助服务仍可抓取节点。` +
                (top ? `<br/><small>前台: ${top}</small>` : '') +
                (layer ? `<br/><small>Secure Layer: ${layer}</small>` : '')
            );
            // Also tag in status area
            statusTags.set('capture', '截图受限');
            renderStatus();
        } else {
            // unknown black screen
            showSecureWarning('⚠️ 截图几乎全黑：可能是抓错 display、或该页面走了 Overlay/受保护渲染。');
            statusTags.set('capture', '截图异常');
            renderStatus();
        }
    } else {
        // looks fine
        hideSecureWarning();
        if (statusTags.has('capture')) {
            statusTags.delete('capture');
            renderStatus();
        }
    }
}

// 绘制点击位置的红色十字准星（仅准星，不显示坐标文字）
function drawClickCrosshair(deviceX, deviceY) {
    const crosshairSize = 40;  // 十字准星大小
    const lineWidth = 2;
    const color = '#ff0000';  // 红色
    
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = lineWidth;
    ctx.setLineDash([5, 5]);  // 虚线效果
    
    // 绘制垂直线
    ctx.beginPath();
    ctx.moveTo(deviceX, deviceY - crosshairSize);
    ctx.lineTo(deviceX, deviceY + crosshairSize);
    ctx.stroke();
    
    // 绘制水平线
    ctx.beginPath();
    ctx.moveTo(deviceX - crosshairSize, deviceY);
    ctx.lineTo(deviceX + crosshairSize, deviceY);
    ctx.stroke();
    
    // 绘制中心点
    ctx.setLineDash([]);  // 实线
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(deviceX, deviceY, 3, 0, 2 * Math.PI);
    ctx.fill();
    
    ctx.restore();
}

// 更新标题栏坐标显示
function updateCoordDisplay(x, y) {
    const coordDisplay = document.getElementById('coordDisplay');
    const coordValue = document.getElementById('coordValue');
    
    if (x !== null && y !== null) {
        coordValue.textContent = `(${Math.round(x)}, ${Math.round(y)})`;
        coordDisplay.classList.remove('hidden');
    } else {
        coordDisplay.classList.add('hidden');
    }
}

async function refreshHierarchy() {
    try {
        const displayId = currentDisplay || "0";
        const useAccessibility = document.getElementById('useAccessibilityService').checked;
        const res = await fetch(`/api/hierarchy?display=${displayId}&force_accessibility=${useAccessibility}`);
        if (!res.ok) return;
        const data = await res.json();
        const parser = new DOMParser();
        const xmlDoc = parser.parseFromString(data.xml, "text/xml");

        treeContainer.innerHTML = '';
        rootNode = xmlDoc.documentElement;
        mapNodeToDom.clear();

        const treeList = document.createElement('div');
        traverseAndBuildTree(rootNode, treeList);
        treeContainer.appendChild(treeList);

        // 显示数据源信息
        if (data.source) {
            const sourceText = data.source === 'accessibility' ? '辅助服务' : 'UIAutomator';
            const reason = data.reason || '';
            let sourceMsg = `📊 数据源: ${sourceText}`;
            
            if (reason === 'uiautomator_incomplete') {
                sourceMsg += ' (UIAutomator数据不完整，自动切换)';
            } else if (reason === 'uiautomator_failed') {
                sourceMsg += ' (UIAutomator失败，使用辅助服务)';
            }
            
            console.log(`[Hierarchy] ${sourceMsg}`);
            
            // 更新右上角状态：数据源 tag
            statusTags.set('source', sourceText);
            renderStatus();
            
            // 如果连接弹窗显示中，添加数据源信息到日志
            const toast = document.getElementById('connectionToast');
            if (toast && toast.classList.contains('show')) {
                const logType = data.source === 'accessibility' ? 'warning' : 'info';
                addLogEntry(sourceMsg, logType);
                
                // 如果使用了辅助服务，添加提示
                if (data.source === 'accessibility') {
                    addLogEntry('⚠️ 注意：辅助服务可能与设备原有服务冲突', 'warning');
                    addLogEntry('💡 提示：点击"重启服务"按钮可恢复原有服务', 'info');
                }
            }
        }

        // 若当前选择辅助服务模式，顺便刷新一下辅助服务状态（避免只显示数据源不显示运行状态）
        if (document.getElementById('useAccessibilityService')?.checked) {
            await updateAccessibilityUIStatus();
        }

        // Restore selection if possible (by text or id?) - skipping for simplicity

    } catch (e) {
        console.error("层级获取失败", e);
        treeContainer.innerHTML = '<div class="empty-state">获取层级数据失败</div>';
    }
}

function getAttributes(xmlNode) {
    if (!xmlNode.attributes) return {};
    const attrs = {};
    for (let i = 0; i < xmlNode.attributes.length; i++) {
        const attr = xmlNode.attributes[i];
        attrs[attr.name] = attr.value;
    }
    return attrs;
}

function parseBounds(boundsStr) {
    if (!boundsStr) return null;
    const matches = boundsStr.match(/\[(\d+),(\d+)\]\[(\d+),(\d+)\]/);
    if (matches) {
        return {
            x1: parseInt(matches[1]),
            y1: parseInt(matches[2]),
            x: parseInt(matches[1]),
            y: parseInt(matches[2]),
            x2: parseInt(matches[3]),
            y2: parseInt(matches[4]),
            w: parseInt(matches[3]) - parseInt(matches[1]),
            h: parseInt(matches[4]) - parseInt(matches[2]),
            area: (parseInt(matches[3]) - parseInt(matches[1])) * (parseInt(matches[4]) - parseInt(matches[2]))
        };
    }
    return null;
}

function traverseAndBuildTree(xmlNode, parentElement) {
    const container = document.createElement('div');
    container.className = 'tree-node';

    const content = document.createElement('div');
    content.className = 'tree-content';

    const attrs = getAttributes(xmlNode);
    let name = attrs['class'] || xmlNode.tagName;
    if (name.includes('.')) {
        name = name.split('.').pop();
    }

    let label = name;
    if (attrs['resource-id']) {
        const id = attrs['resource-id'].split('/').pop();
        label += ` #${id}`;
    }

    if (attrs['text']) {
        const txt = attrs['text'];
        label += ` "${txt.length > 20 ? txt.substring(0, 20) + '...' : txt}"`;
    }

    // NOTE: merged hierarchy root is <hierarchy>, its children are <node>. Use children length, not node-only.
    const children = Array.from(xmlNode.children).filter(c => c.tagName === 'node');

    // Toggle Icon
    const toggle = document.createElement('span');
    toggle.className = 'toggle-btn';

    // If node itself has children, enable toggle.
    // For the root <hierarchy>, it will also have children, but its tagName is 'hierarchy' not 'node'.
    const isRootHierarchy = xmlNode.tagName === 'hierarchy';

    if (children.length > 0) {
        toggle.innerText = '+';
        toggle.onclick = (e) => {
            e.stopPropagation();
            const childContainer = container.querySelector('.children-container');
            if (childContainer.style.display === 'none') {
                childContainer.style.display = 'block';
                toggle.innerText = '-';
            } else {
                childContainer.style.display = 'none';
                toggle.innerText = '+';
            }
        };
    } else {
        toggle.innerHTML = '&bull;';
        toggle.style.cursor = 'default';
        toggle.style.opacity = '0.5';
    }
    content.appendChild(toggle);

    const textSpan = document.createElement('span');
    textSpan.className = 'node-text';
    
    // 应用搜索高亮 - 每个关键字独立配色
    if (searchSettings.patterns && searchSettings.patterns.length > 0) {
        let highlighted = label;
        let matchedPattern = null;
        
        // 找到第一个匹配的pattern
        for (const pattern of searchSettings.patterns) {
            if (pattern && pattern.text && pattern.text.trim() !== '') {
                if (textMatches(label, pattern.text, searchSettings.ignoreCase)) {
                    matchedPattern = pattern;
                    break;
                }
            }
        }
        
        // 如果有匹配，应用该pattern的颜色和高亮
        if (matchedPattern) {
            highlighted = highlightTextWithColor(highlighted, matchedPattern.text, matchedPattern.foreColor, searchSettings.ignoreCase);
            content.style.backgroundColor = matchedPattern.backColor;
        }
        
        textSpan.innerHTML = highlighted;
    } else {
        textSpan.innerText = label;
    }
    
    content.appendChild(textSpan);

    content.onclick = (e) => {
        e.stopPropagation(); // 阻止事件冒泡到父节点
        document.querySelectorAll('.tree-content.selected').forEach(el => el.classList.remove('selected'));
        content.classList.add('selected');
        selectNode(xmlNode);
    };

    content.onmouseover = (e) => {
        hoverNode = xmlNode;
        drawScreen();
        e.stopPropagation(); // Only highlight this node, not parent
    };

    content.onmouseleave = (e) => {
        if (hoverNode === xmlNode) {
            hoverNode = null;
            drawScreen();
        }
    };

    mapNodeToDom.set(xmlNode, { container, content, toggle });

    // 存储搜索辅助属性，供 filterTreeNodes / findAndJumpToNode 使用
    content.dataset.resourceId = attrs['resource-id'] || '';
    content.dataset.contentDesc = attrs['content-desc'] || '';
    content._xmlNode = xmlNode; // DOM → xmlNode 反向查找

    container.appendChild(content);

    if (children.length > 0) {
        const childContainer = document.createElement('div');
        childContainer.className = 'children-container';
        // Root <hierarchy> 默认展开，避免用户以为“卡住了”
        childContainer.style.display = isRootHierarchy ? 'block' : 'none';
        if (isRootHierarchy) {
            toggle.innerText = '-';
        }
        children.forEach(child => traverseAndBuildTree(child, childContainer));
        container.appendChild(childContainer);
    }

    parentElement.appendChild(container);
}

function selectNode(xmlNode) {
    selectedNode = xmlNode;
    const attrs = getAttributes(xmlNode);
    renderProperties(attrs);
    drawScreen();
    // Use timeout to allow UI update before heavy scroll operation if needed
    setTimeout(() => expandToNode(xmlNode), 0);
}

function expandToNode(xmlNode) {
    if (!xmlNode) return;

    // 1. Walk up and expand all parents
    let parent = xmlNode.parentNode;
    // Check if we have mapped this parent, regardless of tagName (handles root 'hierarchy' tag)
    while (parent && mapNodeToDom.has(parent)) {
        const parentDom = mapNodeToDom.get(parent);
        if (parentDom) {
            const childContainer = parentDom.container.querySelector('.children-container');
            if (childContainer && childContainer.style.display === 'none') {
                childContainer.style.display = 'block';
                parentDom.toggle.innerText = '-';
            }
        }
        parent = parent.parentNode;
    }

    // 2. Select and Scroll
    const domRefs = mapNodeToDom.get(xmlNode);
    if (domRefs && domRefs.content) {
        document.querySelectorAll('.tree-content.selected').forEach(el => el.classList.remove('selected'));
        domRefs.content.classList.add('selected');

        // Wait for expansion animation/paint
        setTimeout(() => {
            domRefs.content.scrollIntoView({ block: 'center', inline: 'nearest' });
        }, 50);
    }
}

function getNodePath(xmlNode) {
    const path = [];
    let current = xmlNode;
    while (current && current.tagName === 'node') {
        const attrs = getAttributes(current);
        let name = attrs['class'] || 'Node';
        if (name.includes('.')) name = name.split('.').pop();
        path.unshift({ name: name, node: current });
        current = current.parentNode;
    }
    return path;
}

function renderProperties(attrs) {
    propsContainer.innerHTML = '';

    if (!selectedNode) {
        propsContainer.innerHTML = '<div class="empty-state">请点击元素查看属性</div>';
        return;
    }

    // Render Breadcrumbs
    const path = getNodePath(selectedNode);
    const breadcrumbs = document.createElement('div');
    breadcrumbs.className = 'breadcrumbs';

    path.forEach((item, index) => {
        const crumb = document.createElement('span');
        crumb.className = 'crumb';
        if (index === path.length - 1) crumb.classList.add('active');
        crumb.innerText = item.name;
        crumb.title = "点击选择此父级节点";
        crumb.onclick = () => selectNode(item.node);

        breadcrumbs.appendChild(crumb);

        if (index < path.length - 1) {
            const sep = document.createElement('span');
            sep.className = 'crumb-separator';
            sep.innerText = '>';
            breadcrumbs.appendChild(sep);
        }
    });
    propsContainer.appendChild(breadcrumbs);

    // Render Table
    if (Object.keys(attrs).length === 0) {
        const msg = document.createElement('div');
        msg.className = 'empty-state';
        msg.innerText = '无属性数据';
        propsContainer.appendChild(msg);
        return;
    }

    const table = document.createElement('table');
    table.id = 'props-table';
    let html = '';
    const sortedKeys = Object.keys(attrs).sort();
    
    // 应用搜索高亮到属性面板 - 每个关键字独立配色
    const hasPatterns = searchSettings.patterns && searchSettings.patterns.length > 0;
    
    for (const key of sortedKeys) {
        const value = attrs[key];
        let keyHtml = key;
        let valueHtml = value;
        let rowStyle = '';
        let matchedPattern = null;
        
        if (hasPatterns) {
            // 找到第一个匹配的 pattern
            for (const pattern of searchSettings.patterns) {
                if (pattern && pattern.text && pattern.text.trim() !== '') {
                    const keyMatch = textMatches(key, pattern.text, searchSettings.ignoreCase);
                    const valueMatch = textMatches(value, pattern.text, searchSettings.ignoreCase);
                    
                    if (keyMatch || valueMatch) {
                        matchedPattern = pattern;
                        if (keyMatch) {
                            keyHtml = highlightTextWithColor(keyHtml, pattern.text, pattern.foreColor, searchSettings.ignoreCase);
                        }
                        if (valueMatch) {
                            valueHtml = highlightTextWithColor(valueHtml, pattern.text, pattern.foreColor, searchSettings.ignoreCase);
                        }
                        break; // 使用第一个匹配的 pattern
                    }
                }
            }
            
            // 应用匹配 pattern 的背景色
            if (matchedPattern) {
                rowStyle = ` style="background-color: ${matchedPattern.backColor};"`;
            }
        }
        
        html += `<tr${rowStyle}><th>${keyHtml}</th><td>${valueHtml}</td></tr>`;
    }
    
    table.innerHTML = html;
    propsContainer.appendChild(table);
}

// 搜索匹配辅助函数
function textMatches(text, pattern, ignoreCase) {
    if (!text || !pattern) return false;
    const searchText = ignoreCase ? text.toLowerCase() : text;
    const searchPattern = ignoreCase ? pattern.toLowerCase() : pattern;
    return searchText.includes(searchPattern);
}

// 使用指定颜色高亮文本
function highlightTextWithColor(text, pattern, foreColor, ignoreCase) {
    if (!text || !pattern) return text;
    
    const flags = ignoreCase ? 'gi' : 'g';
    const regex = new RegExp(pattern.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), flags);
    
    return text.replace(regex, (match) => {
        return `<span style="color: ${foreColor}; font-weight: bold; text-decoration: underline;">${match}</span>`;
    });
}

function drawHighlight(xmlNode, strokeColor = '#ef4444', fillColor = 'rgba(239, 68, 68, 0.2)', scale = 1) {
    const attrs = getAttributes(xmlNode);
    if (!attrs['bounds']) return;
    const b = parseBounds(attrs['bounds']);
    if (!b) return;

    // Canvas内部尺寸 = 设备分辨率，bounds坐标直接对应Canvas坐标
    const x = b.x * scale;
    const y = b.y * scale;
    const w = b.w * scale;
    const h = b.h * scale;

    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = 2 * scale;
    ctx.strokeRect(x, y, w, h);

    ctx.fillStyle = fillColor;
    ctx.fillRect(x, y, w, h);
}

// Interaction Variables
let isDragging = false;
let startX = 0;
let startY = 0;
let dragThreshold = 10; // Pixels to consider as drag
let dragStartTime = 0;

function getCanvasCoords(e) {
    const rect = canvas.getBoundingClientRect();

    // 点击位置相对于Canvas显示区域的坐标
    const clickX = e.clientX - rect.left;
    const clickY = e.clientY - rect.top;

    // 坐标映射：点击坐标直接映射到设备物理坐标
    // deviceCoord = clickCoord × (deviceResolution / displaySize)
    const scaleX = screenImage.naturalWidth / rect.width;
    const scaleY = screenImage.naturalHeight / rect.height;

    const deviceX = clickX * scaleX;
    const deviceY = clickY * scaleY;

    return { x: deviceX, y: deviceY };
}

canvas.onmousedown = (e) => {
    // Only handle left click (0) for dragging
    if (e.button !== 0) return;
    isDragging = true;
    const coords = getCanvasCoords(e);
    startX = coords.x;
    startY = coords.y;
    dragStartTime = new Date().getTime();
};

// Right-click for BACK
canvas.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    const realControl = document.getElementById('realControl');
    if (realControl && realControl.checked) {
        performRealBack();
    }
    return false;
});

canvas.onmousemove = (e) => {
    const coords = getCanvasCoords(e);
    // Draw Hover Logic (Only if not dragging)
    if (!isDragging) {
        if (rootNode) {
            const allHits = findAllNodesAt(rootNode, coords.x, coords.y);
            const topNode = pickBestNode(allHits);
            if (topNode !== hoverNode) {
                hoverNode = topNode;
                drawScreen();
            }
            canvas.style.cursor = hoverNode ? 'pointer' : 'default';
        }
    }
};

canvas.onmouseup = (e) => {
    const coords = getCanvasCoords(e);
    
    // 如果不是拖拽状态，处理为简单点击
    if (!isDragging) {
        const realControl = document.getElementById('realControl');
        const isRealControl = realControl && realControl.checked;
        handleClick(coords.x, coords.y, isRealControl);
        return;
    }
    
    isDragging = false;

    const endX = coords.x;
    const endY = coords.y;
    const dist = Math.sqrt(Math.pow(endX - startX, 2) + Math.pow(endY - startY, 2));

    const realControl = document.getElementById('realControl');
    const isRealControl = realControl && realControl.checked;

    if (dist > dragThreshold && isRealControl) {
        // It's a swipe (and real control is on)
        const duration = (new Date().getTime() - dragStartTime) / 1000;
        performRealSwipe(startX, startY, endX, endY, Math.max(0.1, duration));
    } else {
        // It's a click (or swipe but real control off, treat as click to select)
        handleClick(coords.x, coords.y, isRealControl);
    }
};

// Canvas Mouse Leave (Cancel Drag)
canvas.onmouseleave = () => {
    isDragging = false;
    hoverNode = null;
    drawScreen();
};

canvas.onclick = null; // Remove old onclick handler in favor of mouseup logic

function handleClick(x, y, isRealControl) {
    // 显示点击位置的十字准星
    lastClickX = x;
    lastClickY = y;
    
    // 更新标题栏坐标显示
    updateCoordDisplay(x, y);
    
    // 清除之前的定时器
    if (clickCrosshairTimeout) {
        clearTimeout(clickCrosshairTimeout);
    }
    
    // 3秒后隐藏十字准星和坐标显示
    clickCrosshairTimeout = setTimeout(() => {
        lastClickX = null;
        lastClickY = null;
        updateCoordDisplay(null, null);
        drawScreen();
    }, 3000);
    
    // 立即重绘显示十字准星
    drawScreen();
    
    // 1. Real Control Logic
    if (isRealControl) {
        performRealClick(x, y);
    }

    // 2. Inspection Logic (Always inspect on click)
    if (rootNode) {
        const allHits = findAllNodesAt(rootNode, x, y);
        console.log(`[HandleClick] 点击坐标 (${x}, ${y}), 找到 ${allHits.length} 个匹配节点`);
        console.log(`[HandleClick] 设备截图分辨率: ${screenImage.naturalWidth}x${screenImage.naturalHeight}`);
        console.log(`[HandleClick] Canvas显示尺寸: ${canvas.getBoundingClientRect().width}x${canvas.getBoundingClientRect().height}`);
        
        // 打印所有匹配节点的信息
        allHits.forEach((node, index) => {
            const attrs = getAttributes(node);
            const bounds = attrs['bounds'];
            const className = attrs['class'] || 'unknown';
            const resourceId = attrs['resource-id'] || '';
            const text = attrs['text'] || '';
            console.log(`  [${index}] ${className} ${resourceId} bounds=${bounds} text="${text.substring(0, 20)}"`);
        });
        
        const bestNode = pickBestNode(allHits);
        if (bestNode) {
            const attrs = getAttributes(bestNode);
            console.log(`[HandleClick] 选中最佳节点: ${attrs['class'] || 'unknown'} bounds=${attrs['bounds']}`);
            selectNode(bestNode);
        } else {
            console.log(`[HandleClick] ❌ 未找到匹配节点 - 可能的原因：`);
            console.log(`  1. 该位置没有UI元素`);
            console.log(`  2. 坐标映射错误`);
            console.log(`  3. hierarchy数据与截图不同步`);
            
            // 如果没找到节点，打印根节点的bounds供调试
            if (rootNode) {
                const rootAttrs = getAttributes(rootNode.querySelector('node') || rootNode);
                console.log(`  根节点bounds: ${rootAttrs['bounds']}`);
            }
        }
    }
}

async function performRealSwipe(sx, sy, ex, ey, duration) {
    try {
        console.log(`Swiping from (${sx},${sy}) to (${ex},${ey}) in ${duration}s`);
        await fetch('/api/swipe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                start_x: Math.round(sx),
                start_y: Math.round(sy),
                end_x: Math.round(ex),
                end_y: Math.round(ey),
                duration: duration,
                display: parseInt(currentDisplay || 0)
            })
        });
        // Fast refresh after interaction
        setTimeout(refreshScreen, 100);
    } catch (e) {
        console.error("Swipe Failed", e);
    }
}

async function performRealClick(x, y) {
    // Send click to backend
    try {
        await fetch('/api/click', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                x: Math.round(x),
                y: Math.round(y),
                display: parseInt(currentDisplay || 0)
            })
        });
        // 若没有开启自动刷新，手动触发一次截图更新
        if (!document.getElementById('autoRefresh').checked) {
            setTimeout(refreshScreen, 100);
        }
    } catch (e) {
        console.error("Click Failed", e);
    }
}

async function performRealBack() {
    try {
        console.log("Sending Back Keyevent");
        await fetch('/api/back', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                display: parseInt(currentDisplay || 0)
            })
        });
        // Fast refresh after back
        setTimeout(refreshScreen, 100);
    } catch (e) {
        console.error("Back Failed", e);
    }
}

function pickBestNode(allHits) {
    if (!allHits || allHits.length === 0) return null;

    // Sort by Area (Smallest First)
    allHits.sort((a, b) => {
        const aa = getAttributes(a);
        const bb = getAttributes(b);
        const ba = parseBounds(aa['bounds']);
        const bb_bounds = parseBounds(bb['bounds']);

        const areaA = ba ? ba.area : Number.MAX_VALUE;
        const areaB = bb_bounds ? bb_bounds.area : Number.MAX_VALUE;

        return areaA - areaB;
    });
    return allHits[0];
}

function findAllNodesAt(node, x, y) {
    let matches = [];
    const attrs = getAttributes(node);
    const b = parseBounds(attrs['bounds']);

    let inside = false;
    if (b) {
        if (x >= b.x && x <= b.x2 && y >= b.y && y <= b.y2) {
            inside = true;
        }
    }

    const children = Array.from(node.children).filter(c => c.tagName === 'node');
    for (let i = children.length - 1; i >= 0; i--) {
        const childMatches = findAllNodesAt(children[i], x, y);
        matches = matches.concat(childMatches);
    }

    if (inside && node.tagName === 'node') {
        matches.push(node);
    }

    return matches;
}

// Auto Refresh Logic
let isAutoRefreshing = false;
async function autoRefreshTick() {
    const cb = document.getElementById('autoRefresh');
    if (cb && cb.checked && !isAutoRefreshing) {
        isAutoRefreshing = true;
        try {
            await refreshScreen();
        } catch (e) {
            console.error("Auto refresh error:", e);
        } finally {
            isAutoRefreshing = false;
        }
    }
    // "Turbo Mode": No fixed delay. If checked, request next frame immediately.
    // This allows the FPS to be limited only by the ADB/Network speed.
    const delay = (cb && cb.checked) ? 0 : 500;
    setTimeout(autoRefreshTick, delay);
}

// Start the loop
autoRefreshTick();

// 监听实时控制开关，开启时自动启用自动刷新
document.addEventListener('DOMContentLoaded', function() {
    const realControlCheckbox = document.getElementById('realControl');
    const autoRefreshCheckbox = document.getElementById('autoRefresh');
    
    if (realControlCheckbox && autoRefreshCheckbox) {
        realControlCheckbox.addEventListener('change', function() {
            if (this.checked) {
                // 开启实时控制时，自动开启自动刷新
                console.log('[RealControl] 实时控制已开启，自动启用自动刷新');
                autoRefreshCheckbox.checked = true;
            }
        });
    }
});

// 重启服务器函数
async function restartServer() {
    const btn = document.querySelector('.btn-restart');
    if (!btn) return;
    
    // Android Studio 内嵌 JCEF 有时对 confirm/alert 的交互支持不稳定，会导致“点击没反应”。
    // 这里改为：点击即执行，并用 overlay + status 提示。
    
    // 禁用按钮
    btn.disabled = true;
    btn.textContent = '🔄 重启中...';

    // --- 1) 立刻清理UI状态（你希望的“清空现场/关闭截屏页/清空所有选择”） ---
    const overlay = document.getElementById('restartOverlay');
    const subtitleEl = document.getElementById('restartSubtitle');
    const hintEl = document.getElementById('restartHint');
    const barEl = document.getElementById('restartProgressBar');
    const screenEmpty = document.getElementById('screenEmptyState');

    const setOverlayText = (subtitle, hint) => {
        if (subtitleEl) subtitleEl.innerText = subtitle || '';
        if (hintEl) hintEl.innerText = hint || '';
    };

    const setOverlayProgress = (pct) => {
        if (!barEl) return;
        const clamped = Math.max(0, Math.min(100, pct));
        barEl.style.width = `${clamped}%`;
    };

    const showRestartOverlay = () => {
        if (!overlay) return;
        overlay.classList.remove('hidden');
        overlay.classList.remove('long-wait');
        setOverlayProgress(3);
    };

    // allow cancel overlay (pure UI cancel, no network abort)
    const cancelBtn = document.getElementById('restartCancelBtn');
    const reloadBtn = document.getElementById('restartReloadBtn');
    if (cancelBtn) {
        cancelBtn.onclick = () => {
            // 只关闭 overlay，不改变服务状态；适合“点了没想重启/想继续看页面”的情况
            overlay?.classList.add('hidden');
        };
    }
    if (reloadBtn) {
        reloadBtn.classList.add('hidden');
    }

    const markLongWait = () => {
        if (!overlay) return;
        overlay.classList.add('long-wait');
    };

    const clearUIForRestart = () => {
        // reset selection state
        rootNode = null;
        selectedNode = null;
        hoverNode = null;
        mapNodeToDom.clear();

        // clear panels
        treeContainer.innerHTML = '<div class="empty-state">暂无层级数据</div>';
        propsContainer.innerHTML = '<div class="empty-state">请点击元素查看属性</div>';

        // close modals if any
        document.getElementById('deviceModal')?.classList.remove('show');
        document.getElementById('displayModal')?.classList.remove('show');
        document.getElementById('settingsModal')?.classList.remove('show');

        // hide toast
        closeToast();

        // hide coord and secure warning
        updateCoordDisplay(null, null);
        hideSecureWarning();

        // clear last click crosshair
        lastClickX = null;
        lastClickY = null;
        if (clickCrosshairTimeout) {
            clearTimeout(clickCrosshairTimeout);
            clickCrosshairTimeout = null;
        }

        // reset status
        statusBaseText = '未连接';
        statusTags.clear();
        renderStatus();
        const statusEl = document.getElementById('status');
        if (statusEl) {
            statusEl.classList.add('status-badge');
            statusEl.style.color = '';
            statusEl.style.fontWeight = '';
        }

        // reset device + display selection
        currentDevice = null;
        currentDisplay = '0';
        const deviceText = document.getElementById('deviceSelectText');
        if (deviceText) deviceText.innerText = '请选择设备';
        const displayText = document.getElementById('displaySelectText');
        if (displayText) displayText.innerText = '默认屏幕 (0)';
        disableDisplaySelector();

        // close screenshot page (hide canvas via empty state)
        if (screenEmpty) screenEmpty.classList.remove('hidden');
        // Clear canvas
        try {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            canvas.width = 1;
            canvas.height = 1;
        } catch (e) {
            // ignore
        }
        screenImage = new Image();
        canvas.style.cursor = 'default';
    };

    showRestartOverlay();
    setOverlayText('正在清空现场...', '正在断开连接并准备重启服务…');
    clearUIForRestart();
    setOverlayProgress(12);
    
    try {
        // --- 2) 如果当前处于“辅助服务模式”，先断开辅助服务（你希望的“断开辅助服务”） ---
        const a11ySwitch = document.getElementById('useAccessibilityService');
        const isA11yMode = !!(a11ySwitch && a11ySwitch.checked);

        if (isA11yMode) {
            setOverlayText('正在断开辅助服务...', '执行 /api/accessibility/disable');
            setOverlayProgress(22);
            try {
                const disableRes = await fetch('/api/accessibility/disable', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                });
                // 不强依赖成功；失败也继续重启
                if (!disableRes.ok) {
                    console.warn('[RestartServer] /api/accessibility/disable failed:', await disableRes.text());
                }
            } catch (e) {
                console.warn('[RestartServer] disable accessibility failed:', e);
            } finally {
                // UI 层面关闭开关
                a11ySwitch.checked = false;
                const dataSourceLabel = document.getElementById('dataSourceLabel');
                if (dataSourceLabel) dataSourceLabel.textContent = 'UIAutomator';
            }
        }

        // --- 3) 发起后端重启请求 ---
        setOverlayText('正在请求重启服务...', '发送 /api/restart-server');
        setOverlayProgress(32);

        console.log('[RestartServer] 发送重启请求...');
        const response = await fetch('/api/restart-server', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        
        if (response.ok) {
            const data = await response.json();
            console.log('[RestartServer] 服务器正在重启:', data);

            setOverlayText('服务器已进入重启流程...', '正在等待服务恢复响应…');
            setOverlayProgress(45);
            
            // 显示等待消息
            const statusEl = document.getElementById('status');
            if (statusEl) {
                statusEl.innerText = '服务器重启中...';
                statusEl.style.color = '#f59e0b';
            }
            
            // 开始轮询检查服务器是否恢复
            let checkAttempts = 0;
            // 插件监控线程：5秒一次，连续失败3次才重启 => 最少约15秒才开始拉起。
            // 这里把等待拉长，避免前端“误判超时”。
            const maxAttempts = 60; // 最多等待60秒

            // 如果等待超过 10 秒，启用“毁灭进度”抖动
            const longWaitTimer = setTimeout(() => {
                markLongWait();
                setOverlayText('正在毁灭进度（等待服务复活）...', '时间有点久，但我会一直盯着…');
            }, 10000);
            
            const checkServer = async () => {
                checkAttempts++;
                console.log(`[RestartServer] 检查服务器状态... (${checkAttempts}/${maxAttempts})`);

                // 进度条：45% -> 95% 之间缓慢推进
                const p = 45 + Math.floor((checkAttempts / maxAttempts) * 50);
                setOverlayProgress(p);
                if (subtitleEl) subtitleEl.innerText = `等待服务恢复响应... (${checkAttempts}/${maxAttempts})`;
                
                try {
                    const testResponse = await fetch('/api/devices', {
                        method: 'GET',
                        cache: 'no-cache'
                    });
                    
                    if (testResponse.ok) {
                        console.log('[RestartServer] ✅ 服务器已恢复！');
                        if (statusEl) {
                            statusEl.innerText = '服务器已重启';
                            statusEl.style.color = '#10b981';
                        }
                        btn.disabled = false;
                        btn.textContent = '🔄 重启服务';

                        // overlay 收尾
                        clearTimeout(longWaitTimer);
                        setOverlayText('✅ 服务已恢复', '即将刷新页面…');
                        setOverlayProgress(100);

                        // show reload button (avoid alert)
                        if (reloadBtn) reloadBtn.classList.remove('hidden');
                        
                        // 显示成功消息并刷新页面
                        setTimeout(() => window.location.reload(), 600);
                        return;
                    }
                } catch (e) {
                    // 服务器还没恢复，继续等待
                }
                
                if (checkAttempts < maxAttempts) {
                    // 继续检查
                    setTimeout(checkServer, 1000);
                } else {
                    // 超时
                    console.error('[RestartServer] ❌ 重启超时');
                    if (statusEl) {
                        statusEl.innerText = '重启超时，请手动刷新页面';
                        statusEl.style.color = '#ef4444';
                    }
                    btn.disabled = false;
                    btn.textContent = '🔄 重启服务';
                    clearTimeout(longWaitTimer);
                    setOverlayText('❌ 等待超时', '你可以手动刷新（F5）或重新打开工具窗口');
                    setOverlayProgress(100);

                    // show reload button for manual action
                    if (reloadBtn) reloadBtn.classList.remove('hidden');
                }
            };
            
            // 等待2秒后开始检查（给服务器时间停止和重启）
            setTimeout(checkServer, 2000);
            
        } else {
            throw new Error('重启请求失败');
        }
    } catch (e) {
        console.error('[RestartServer] 重启失败:', e);
        btn.disabled = false;
        btn.textContent = '🔄 重启服务';

        // overlay 失败提示
        if (overlay) {
            overlay.classList.add('long-wait');
            setOverlayText('❌ 重启失败', e.message || '未知错误');
            setOverlayProgress(100);
        }

        // show reload button for manual action
        const reloadBtn = document.getElementById('restartReloadBtn');
        if (reloadBtn) reloadBtn.classList.remove('hidden');
    }
}

// =========================
// ☠️ Hard Reset: 立刻硬清空
// =========================

async function clearBrowserStateAggressively() {
    // 1) Clear app local storage (includes uiInspectorSettings)
    try { localStorage.clear(); } catch (e) {}
    try { sessionStorage.clear(); } catch (e) {}

    // 2) Clear CacheStorage (service worker caches) if any
    try {
        if (window.caches && caches.keys) {
            const keys = await caches.keys();
            await Promise.all(keys.map(k => caches.delete(k)));
        }
    } catch (e) {
        // ignore
    }

    // 3) best-effort unregister service workers
    try {
        if (navigator.serviceWorker && navigator.serviceWorker.getRegistrations) {
            const regs = await navigator.serviceWorker.getRegistrations();
            await Promise.all(regs.map(r => r.unregister()));
        }
    } catch (e) {
        // ignore
    }
}

function clearUIForHardReset() {
    const overlay = document.getElementById('restartOverlay');
    const subtitleEl = document.getElementById('restartSubtitle');
    const hintEl = document.getElementById('restartHint');
    const barEl = document.getElementById('restartProgressBar');
    const screenEmpty = document.getElementById('screenEmptyState');

    const setOverlayText = (subtitle, hint) => {
        if (subtitleEl) subtitleEl.innerText = subtitle || '';
        if (hintEl) hintEl.innerText = hint || '';
    };
    const setOverlayProgress = (pct) => {
        if (!barEl) return;
        const clamped = Math.max(0, Math.min(100, pct));
        barEl.style.width = `${clamped}%`;
    };

    if (overlay) {
        overlay.classList.remove('hidden');
        overlay.classList.remove('long-wait');
    }
    setOverlayText('☠️ 硬清空：正在瞬间清理前端状态...', '将立即杀死后端进程，并等待服务自动复活');
    setOverlayProgress(10);

    // reset selection state
    rootNode = null;
    selectedNode = null;
    hoverNode = null;
    mapNodeToDom.clear();

    // clear panels
    treeContainer.innerHTML = '<div class="empty-state">暂无层级数据</div>';
    propsContainer.innerHTML = '<div class="empty-state">请点击元素查看属性</div>';

    // close modals if any
    document.getElementById('deviceModal')?.classList.remove('show');
    document.getElementById('displayModal')?.classList.remove('show');
    document.getElementById('settingsModal')?.classList.remove('show');

    // hide toast
    closeToast();

    // hide coord and secure warning
    updateCoordDisplay(null, null);
    hideSecureWarning();

    // clear last click crosshair
    lastClickX = null;
    lastClickY = null;
    if (clickCrosshairTimeout) {
        clearTimeout(clickCrosshairTimeout);
        clickCrosshairTimeout = null;
    }

    // reset status
    statusBaseText = '未连接';
    statusTags.clear();
    renderStatus();
    const statusEl = document.getElementById('status');
    if (statusEl) {
        statusEl.classList.add('status-badge');
        statusEl.style.color = '';
        statusEl.style.fontWeight = '';
    }

    // reset device + display selection
    currentDevice = null;
    currentDisplay = '0';

    // stop continuous background requests
    try {
        const autoRefreshEl = document.getElementById('autoRefresh');
        if (autoRefreshEl) autoRefreshEl.checked = false;
        const realControlEl = document.getElementById('realControl');
        if (realControlEl) realControlEl.checked = false;
    } catch (e) {
        // ignore
    }
    const deviceText = document.getElementById('deviceSelectText');
    if (deviceText) deviceText.innerText = '请选择设备';
    const displayText = document.getElementById('displaySelectText');
    if (displayText) displayText.innerText = '默认屏幕 (0)';
    disableDisplaySelector();

    // close screenshot page (hide canvas via empty state)
    if (screenEmpty) screenEmpty.classList.remove('hidden');
    // Clear canvas
    try {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        canvas.width = 1;
        canvas.height = 1;
    } catch (e) {}
    screenImage = new Image();
    canvas.style.cursor = 'default';

    // cancel / reload buttons in overlay
    const cancelBtn = document.getElementById('restartCancelBtn');
    const reloadBtn = document.getElementById('restartReloadBtn');
    if (cancelBtn) {
        cancelBtn.disabled = false;
        cancelBtn.onclick = () => overlay?.classList.add('hidden');
    }
    if (reloadBtn) reloadBtn.classList.add('hidden');
}

async function waitServerUpAndReload(maxSeconds = 45) {
    const subtitleEl = document.getElementById('restartSubtitle');
    const hintEl = document.getElementById('restartHint');
    const barEl = document.getElementById('restartProgressBar');
    const reloadBtn = document.getElementById('restartReloadBtn');

    const setText = (subtitle, hint) => {
        if (subtitleEl) subtitleEl.innerText = subtitle || '';
        if (hintEl) hintEl.innerText = hint || '';
    };
    const setProgress = (pct) => {
        if (!barEl) return;
        const clamped = Math.max(0, Math.min(100, pct));
        barEl.style.width = `${clamped}%`;
    };

    // poll
    const maxAttempts = Math.max(5, maxSeconds);
    for (let i = 1; i <= maxAttempts; i++) {
        setText(`等待服务复活... (${i}/${maxAttempts})`, '检测 /api/devices 可用后自动刷新');
        setProgress(35 + Math.floor((i / maxAttempts) * 55));
        try {
            const res = await fetch('/api/devices', { cache: 'no-cache' });
            if (res.ok) {
                setText('✅ 服务已恢复', '正在安全刷新页面…');
                setProgress(100);
                setTimeout(() => window.location.reload(), 300);
                return;
            }
        } catch (e) {
            // ignore
        }
        await new Promise(r => setTimeout(r, 1000));
    }

    // timeout
    setText('❌ 等待超时', '你可以点“刷新页面”再试一次，或关闭/重开工具窗口');
    setProgress(100);
    if (reloadBtn) reloadBtn.classList.remove('hidden');
}

// 按钮入口：立刻硬清空（清 UI + 清存储 + 让后端自杀）
async function hardResetAll() {
    const btn = document.querySelector('.btn-hard-reset');
    const restartBtn = document.querySelector('.btn-restart');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '☠️ 清空中...';
    }
    if (restartBtn) restartBtn.disabled = true;

    // 1) 立刻清 UI（用户体感：瞬间断电）
    clearUIForHardReset();

    // 2) 清除前端持久化（尽可能）
    const barEl = document.getElementById('restartProgressBar');
    if (barEl) barEl.style.width = '20%';
    await clearBrowserStateAggressively();

    // 3) 如果原来开了辅助服务模式，尽量先 disable 一下（best-effort）
    const a11ySwitch = document.getElementById('useAccessibilityService');
    const isA11yMode = !!(a11ySwitch && a11ySwitch.checked);
    if (isA11yMode) {
        try {
            await fetch('/api/accessibility/disable', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
        } catch (e) {}
        // UI 层面关闭开关
        a11ySwitch.checked = false;
        const dataSourceLabel = document.getElementById('dataSourceLabel');
        if (dataSourceLabel) dataSourceLabel.textContent = 'UIAutomator';
    }

    // 4) 让后端立刻自杀（会导致本请求可能失败/断开，这就是我们要的“瞬间杀死”）
    const subtitleEl = document.getElementById('restartSubtitle');
    const hintEl = document.getElementById('restartHint');
    if (subtitleEl) subtitleEl.innerText = '正在强制杀死后端进程...';
    if (hintEl) hintEl.innerText = '发送 /api/hard-exit（服务将立即断开）';
    if (barEl) barEl.style.width = '32%';

    try {
        // 这个请求大概率会在 JCEF 里“断开”，属于正常现象
        await fetch('/api/hard-exit', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
    } catch (e) {
        // ignore
    }

    // 5) 等待插件侧监控/重建把服务拉起来，然后安全 reload
    if (hintEl) hintEl.innerText = '后端已被杀死（或正在死亡），等待服务复活...';
    if (barEl) barEl.style.width = '40%';
    await waitServerUpAndReload(60);

    // 兜底：如果没 reload 就恢复按钮
    if (btn) {
        btn.disabled = false;
        btn.textContent = '☠️ 硬清空';
    }
    if (restartBtn) restartBtn.disabled = false;
}

// 安全刷新：避免服务未恢复时直接 reload 导致 JCEF 黑屏卡死
async function safeReloadAfterServerUp() {
    const overlay = document.getElementById('restartOverlay');
    const subtitleEl = document.getElementById('restartSubtitle');
    const hintEl = document.getElementById('restartHint');
    const barEl = document.getElementById('restartProgressBar');
    const reloadBtn = document.getElementById('restartReloadBtn');
    const cancelBtn = document.getElementById('restartCancelBtn');

    const setText = (subtitle, hint) => {
        if (subtitleEl) subtitleEl.innerText = subtitle || '';
        if (hintEl) hintEl.innerText = hint || '';
    };

    const setProgress = (pct) => {
        if (!barEl) return;
        const clamped = Math.max(0, Math.min(100, pct));
        barEl.style.width = `${clamped}%`;
    };

    if (overlay) overlay.classList.remove('hidden');
    if (reloadBtn) reloadBtn.classList.add('hidden');
    if (cancelBtn) cancelBtn.disabled = true;

    setText('安全刷新：等待服务恢复...', '检测 /api/devices 可用后再刷新页面');
    setProgress(60);

    const maxAttempts = 30; // 30s
    for (let i = 1; i <= maxAttempts; i++) {
        try {
            const res = await fetch('/api/devices', { cache: 'no-cache' });
            if (res.ok) {
                setText('✅ 服务已恢复', '正在刷新页面…');
                setProgress(100);
                setTimeout(() => window.location.reload(), 300);
                return;
            }
        } catch (e) {
            // ignore
        }

        setProgress(60 + Math.floor((i / maxAttempts) * 35));
        if (subtitleEl) subtitleEl.innerText = `安全刷新：等待服务恢复... (${i}/${maxAttempts})`;
        await new Promise(r => setTimeout(r, 1000));
    }

    // timeout
    setText('❌ 服务仍未恢复', '建议：稍后再点一次“刷新页面”，或直接重开工具窗口');
    setProgress(100);
    if (cancelBtn) cancelBtn.disabled = false;
    if (reloadBtn) reloadBtn.classList.remove('hidden');
}

// Resizable Panels Logic
const splitter = document.getElementById('sidebarSplitter');
const propsPanel = document.getElementById('propsPanel');
const sidebar = document.querySelector('.sidebar');
let isResizing = false;
let isResizingH = false;

// Horizontal Resizing (Main Splitter)
const mainSplitter = document.getElementById('mainSplitter');
const mainSidebar = document.getElementById('sidebar');

if (mainSplitter && mainSidebar) {
    mainSplitter.addEventListener('mousedown', (e) => {
        isResizingH = true;
        document.body.classList.add('resizing-h');
        mainSplitter.classList.add('active');
        e.preventDefault();
    });

    window.addEventListener('mousemove', (e) => {
        if (!isResizingH) return;

        // Calculate new width for sidebar (right-to-left)
        const newWidth = window.innerWidth - e.clientX - 20; // 20 for padding

        // Limits
        const minWidth = 280;
        const maxWidth = window.innerWidth * 0.8;

        if (newWidth >= minWidth && newWidth <= maxWidth) {
            mainSidebar.style.width = newWidth + 'px';
        }
    });

    window.addEventListener('mouseup', () => {
        if (isResizingH) {
            isResizingH = false;
            document.body.classList.remove('resizing-h');
            mainSplitter.classList.remove('active');
        }
    });
}

if (splitter && propsPanel && sidebar) {
    splitter.addEventListener('mousedown', (e) => {
        isResizing = true;
        document.body.classList.add('resizing');
        splitter.classList.add('active');

        // Remove flex-grow to allow size setting
        propsPanel.style.flex = 'none';

        // Set initial height explicitly if needed
        if (!propsPanel.style.height) {
            propsPanel.style.height = propsPanel.offsetHeight + 'px';
        }

        e.preventDefault();
    });

    window.addEventListener('mousemove', (e) => {
        if (!isResizing) return;

        // Calculate new height relative to sidebar top
        const sidebarRect = sidebar.getBoundingClientRect();
        let newHeight = e.clientY - sidebarRect.top;

        // Limits
        const minHeight = 100;
        const maxHeight = sidebarRect.height - 100; // Leave space for tree

        if (newHeight < minHeight) newHeight = minHeight;
        if (newHeight > maxHeight) newHeight = maxHeight;

        propsPanel.style.height = newHeight + 'px';
    });

    window.addEventListener('mouseup', () => {
        if (isResizing) {
            isResizing = false;
            document.body.classList.remove('resizing');
            splitter.classList.remove('active');
        }
    });
}

// ── highlightClickableBounds 辅助 ──────────────────────────────────────────────
function _highlightAllClickable(node) {
    if (!node) return;
    const attrs = getAttributes(node);
    if (attrs['clickable'] === 'true') {
        drawHighlight(node, '#22d3ee', 'rgba(34, 211, 238, 0.12)', 1);
    }
    Array.from(node.children).filter(c => c.tagName === 'node').forEach(child => _highlightAllClickable(child));
}

// ── 缩放功能 ──────────────────────────────────────────────────────────────────
let zoomScale = 1.0;

function zoomIn() {
    zoomScale = Math.min(zoomScale + 0.25, 4.0);
    applyZoom();
}

function zoomOut() {
    zoomScale = Math.max(zoomScale - 0.25, 0.25);
    applyZoom();
}

function resetZoom() {
    zoomScale = 1.0;
    applyZoom();
}

function applyZoom() {
    const label = document.getElementById('zoomLabel');
    if (zoomScale === 1.0) {
        canvas.classList.remove('original-size');
        canvas.style.width = '';
        canvas.style.height = '';
    } else {
        canvas.classList.add('original-size');
        canvas.style.width = (canvas.width * zoomScale) + 'px';
        canvas.style.height = (canvas.height * zoomScale) + 'px';
    }
    if (label) label.textContent = Math.round(zoomScale * 100) + '%';
}

// ── APK 安装辅助弹窗 ───────────────────────────────────────────────────────────
function showApkInstallModal() {
    const modal = document.getElementById('apkInstallModal');
    if (!modal) return;
    const progress = document.getElementById('apkInstallProgress');
    const result   = document.getElementById('apkInstallResult');
    const reconnect = document.getElementById('btnApkReconnect');
    const btnNormal   = document.getElementById('btnInstallNormal');
    const btnNoStream = document.getElementById('btnInstallNoStream');
    if (progress)  progress.style.display = 'none';
    if (result)  { result.style.display = 'none'; result.textContent = ''; }
    if (reconnect) reconnect.style.display = 'none';
    if (btnNormal)   btnNormal.disabled = false;
    if (btnNoStream) btnNoStream.disabled = false;
    modal.classList.add('show');
}

function closeApkInstallModal(event) {
    if (event && event.target !== document.getElementById('apkInstallModal')) return;
    document.getElementById('apkInstallModal')?.classList.remove('show');
}

async function doInstallApk(noStreaming) {
    const progress  = document.getElementById('apkInstallProgress');
    const result    = document.getElementById('apkInstallResult');
    const reconnect = document.getElementById('btnApkReconnect');
    const btnNormal   = document.getElementById('btnInstallNormal');
    const btnNoStream = document.getElementById('btnInstallNoStream');
    if (progress)  progress.style.display = 'block';
    if (result)  { result.style.display = 'none'; result.textContent = ''; }
    if (reconnect) reconnect.style.display = 'none';
    if (btnNormal)   btnNormal.disabled = true;
    if (btnNoStream) btnNoStream.disabled = true;
    try {
        const resp = await fetchWithTimeout('/api/accessibility/install-apk', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ serial: currentDevice?.serial, no_streaming: noStreaming })
        }, 90000);
        const data = await resp.json();
        if (result) {
            result.style.display = 'block';
            result.textContent = data.output || data.error || JSON.stringify(data);
        }
        if (data.success && reconnect) reconnect.style.display = 'block';
    } catch (e) {
        if (result) { result.style.display = 'block'; result.textContent = '安装失败：' + e.message; }
    } finally {
        if (progress) progress.style.display = 'none';
        if (btnNormal)   btnNormal.disabled = false;
        if (btnNoStream) btnNoStream.disabled = false;
    }
}

// ── 轻量级非阻塞通知 ──────────────────────────────────────────────────────────
function showNotice(msg, type = 'info', duration = 4000) {
    const container = document.getElementById('noticeContainer');
    if (!container) { console.warn('[Notice]', msg); return; }
    const iconMap  = { info: 'ℹ️', success: '✅', warning: '⚠️', error: '❌' };
    const colorMap = { info: '#60a5fa', success: '#34d399', warning: '#fbbf24', error: '#f87171' };
    const bar = document.createElement('div');
    bar.className = 'notice-bar';
    bar.style.borderLeftColor = colorMap[type] || colorMap.info;
    bar.innerHTML = `
        <span class="notice-icon">${iconMap[type] || iconMap.info}</span>
        <span class="notice-msg">${msg}</span>
        <button class="notice-close" onclick="this.parentElement.remove()">×</button>
    `;
    container.appendChild(bar);
    requestAnimationFrame(() => requestAnimationFrame(() => bar.classList.add('visible')));
    if (duration > 0) {
        setTimeout(() => {
            bar.classList.remove('visible');
            setTimeout(() => bar.remove(), 300);
        }, duration);
    }
}

// ── 按键面板 ──────────────────────────────────────────────────────────────────
async function sendKey(keyName) {
    if (!currentDevice) { showNotice('请先连接设备', 'warning'); return; }
    try {
        await fetchWithTimeout('/api/send_key', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ device: currentDevice.serial, key: keyName })
        }, 5000);
    } catch (e) {
        showNotice('发送按键失败: ' + e.message, 'error');
    }
}

// ── UI 树过滤 ─────────────────────────────────────────────────────────────────
// 判断一个 .tree-content 是否匹配查询词（节点文本 / resource-id / content-desc）
function _contentMatchesQuery(content, q) {
    const textEl = content.querySelector('.node-text');
    if ((textEl?.innerText || '').toLowerCase().includes(q)) return true;
    if ((content.dataset.resourceId || '').toLowerCase().includes(q)) return true;
    if ((content.dataset.contentDesc || '').toLowerCase().includes(q)) return true;
    return false;
}

function filterTreeNodes(query) {
    const clearBtn = document.getElementById('treeFilterClearBtn');
    if (clearBtn) clearBtn.style.display = query ? 'inline-flex' : 'none';
    const q = (query || '').trim().toLowerCase();
    if (!q) {
        document.querySelectorAll('#tree-container .tree-node').forEach(node => { node.style.display = ''; });
        return;
    }
    document.querySelectorAll('#tree-container .tree-content').forEach(content => {
        const treeNode = content.parentElement;
        if (!treeNode) return;
        if (!_contentMatchesQuery(content, q)) {
            treeNode.style.display = 'none';
        } else {
            treeNode.style.display = '';
            let ancestor = treeNode.parentElement;
            while (ancestor && ancestor.id !== 'tree-container') {
                if (ancestor.classList.contains('tree-node') || ancestor.classList.contains('children-container')) {
                    ancestor.style.display = '';
                }
                ancestor = ancestor.parentElement;
            }
        }
    });
}

function clearTreeFilter() {
    const input = document.getElementById('treeFilter');
    if (input) input.value = '';
    filterTreeNodes('');
}

function expandAllTree() {
    document.querySelectorAll('#tree-container .children-container').forEach(el => { el.style.display = 'block'; });
    document.querySelectorAll('#tree-container .toggle-btn').forEach(btn => { if (btn.innerText === '+') btn.innerText = '-'; });
}

function collapseAllTree() {
    document.querySelectorAll('#tree-container .children-container').forEach(el => { el.style.display = 'none'; });
    document.querySelectorAll('#tree-container .toggle-btn').forEach(btn => { if (btn.innerText === '-') btn.innerText = '+'; });
}

// ── 回车跳转：多匹配时弹选择弹窗 ─────────────────────────────────────────────
function findAndJumpToNode(query) {
    const q = (query || '').trim().toLowerCase();
    if (!q) return;
    const matches = [];
    document.querySelectorAll('#tree-container .tree-content').forEach(content => {
        if (_contentMatchesQuery(content, q)) matches.push(content);
    });
    if (matches.length === 0) { showNotice('未找到匹配节点', 'warning', 2000); return; }
    if (matches.length === 1) {
        const xmlNode = matches[0]._xmlNode;
        if (xmlNode) selectNode(xmlNode);
        return;
    }
    showNodePickerDialog(matches, q);
}

function showNodePickerDialog(matches, query) {
    const dialog = document.getElementById('nodePickerDialog');
    const list   = document.getElementById('nodePickerList');
    const count  = document.getElementById('nodePickerCount');
    if (!dialog || !list) return;
    if (count) count.textContent = `找到 ${matches.length} 个匹配节点`;
    list.innerHTML = '';

    function escHtml(s) {
        return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
    function hl(text, q) {
        if (!text || !q) return escHtml(text);
        const idx = text.toLowerCase().indexOf(q);
        if (idx === -1) return escHtml(text);
        return escHtml(text.slice(0, idx))
            + `<mark class="picker-hl">${escHtml(text.slice(idx, idx + q.length))}</mark>`
            + escHtml(text.slice(idx + q.length));
    }

    matches.forEach((content, idx) => {
        const xmlNode = content._xmlNode;
        const attrs   = xmlNode ? getAttributes(xmlNode) : {};
        const cls     = attrs['class'] || '';
        const shortCls = cls.includes('.') ? cls.split('.').pop() : cls;
        const rid    = content.dataset.resourceId || '';
        const cdesc  = content.dataset.contentDesc || '';
        const txt    = attrs['text'] || '';
        const bounds = attrs['bounds'] || '';

        const item = document.createElement('div');
        item.className = 'node-picker-item';
        item.innerHTML = `
            <div class="picker-index">${idx + 1}</div>
            <div class="picker-detail">
                <div class="picker-cls">${hl(shortCls, query)}</div>
                ${rid   ? `<div class="picker-attr"><span class="picker-key">id</span>${hl(rid, query)}</div>` : ''}
                ${cdesc ? `<div class="picker-attr"><span class="picker-key">desc</span>${hl(cdesc, query)}</div>` : ''}
                ${txt   ? `<div class="picker-attr"><span class="picker-key">text</span>${hl(txt, query)}</div>` : ''}
                ${bounds ? `<div class="picker-bounds">${bounds}</div>` : ''}
            </div>`;
        item.onclick = () => {
            if (xmlNode) selectNode(xmlNode);
            closeNodePickerDialog();
        };
        list.appendChild(item);
    });
    dialog.classList.add('show');
}

function closeNodePickerDialog() {
    document.getElementById('nodePickerDialog')?.classList.remove('show');
}

// ─── 按键浮层面板 ────────────────────────────────────────────────────────────
function toggleKeyPanel() {
    const panel = document.getElementById('keyPanel');
    if (!panel) return;

    const isHidden = panel.classList.contains('hidden');
    if (isHidden) {
        // 定位到触发按钮正下方
        const btn = document.querySelector('[onclick="toggleKeyPanel()"]');
        if (btn) {
            const rect = btn.getBoundingClientRect();
            panel.style.left = rect.left + 'px';
            panel.style.top  = (rect.bottom + 6) + 'px';
        }
        panel.classList.remove('hidden');

        // 点击面板外部自动关闭
        function onOutsideClick(e) {
            const btn2 = document.querySelector('[onclick="toggleKeyPanel()"]');
            if (!panel.contains(e.target) && e.target !== btn2) {
                panel.classList.add('hidden');
                document.removeEventListener('mousedown', onOutsideClick, true);
            }
        }
        // 延迟注册，避免触发当前点击
        setTimeout(() => document.addEventListener('mousedown', onOutsideClick, true), 0);
    } else {
        panel.classList.add('hidden');
    }
}
