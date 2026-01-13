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
        
        if (d.ss_type) {
            const badge = document.createElement('span');
            badge.className = 'modal-item-badge';
            badge.innerText = d.ss_type;
            item.appendChild(badge);
        }
        
        item.onclick = () => {
            selectDevice(d);
            closeDeviceModal();
        };
        
        listContainer.appendChild(item);
    });
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
    const ssLabel = device.ss_type ? ` [${device.ss_type}]` : '';
    btn.innerText = `${device.model} (${device.serial})${ssLabel}`;
    onDeviceChanged();
}

function selectDisplay(displayId, description) {
    currentDisplay = displayId;
    const btn = document.getElementById('displaySelectText');
    btn.innerText = description;
    refreshSnapshot();
}

// Init
window.onload = () => {
    refreshDeviceList();
};

// Track if we've already auto-connected to avoid repeated connections
let hasAutoConnected = false;

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

async function refreshDeviceList(autoConnect = false) {
    console.log("[RefreshDeviceList] 开始获取设备列表... autoConnect:", autoConnect);
    const btn = document.getElementById('deviceSelectText');
    btn.innerText = '正在获取设备...';
    
    try {
        const res = await fetch('/api/devices');
        devicesList = await res.json();
        console.log("[RefreshDeviceList] 获取到设备:", devicesList);

        if (devicesList.length === 0) {
            console.log("[RefreshDeviceList] 没有发现设备");
            btn.innerText = '未发现设备';
            return;
        }

        // Select first device by default
        if (devicesList.length > 0 && !currentDevice) {
            currentDevice = devicesList[0];
            selectDevice(currentDevice);
        }
        
        // Auto-connect if requested
        if (autoConnect || !hasAutoConnected) {
            const firstDevice = devicesList[0];
            hasAutoConnected = true;
            console.log(`[AutoConnect] 自动连接到: ${firstDevice.serial}`);
            
            clearLog();
            showToast();
            
            const statusEl = document.getElementById('status');
            statusEl.innerText = '正在连接...';
            statusEl.style.color = '#f59e0b';
            
            addLogEntry(`🔍 检测到设备: ${firstDevice.model}`, 'info');
            addLogEntry(`📱 Serial: ${firstDevice.serial}`, 'info');
            if (firstDevice.ss_type) {
                addLogEntry(`⚙️ 设备类型: ${firstDevice.ss_type} (需要初始化)`, 'warning');
            } else {
                addLogEntry(`✅ 普通Android设备`, 'info');
            }
            
            currentDevice = firstDevice;
            selectDevice(firstDevice);
            setTimeout(() => connectDevice(), 500);
        }
    } catch (e) {
        console.error("[RefreshDeviceList] 错误:", e);
        const statusEl = document.getElementById('status');
        statusEl.innerText = `获取设备失败: ${e.message}`;
        statusEl.style.color = '#ef4444';
        btn.innerText = '获取设备失败';
        
        clearLog();
        showToast();
        addLogEntry(`❌ 获取设备失败: ${e.message}`, 'error');
    }
}

async function onDeviceChanged() {
    if (!currentDevice) return;
    console.log("Device changed to:", currentDevice.serial);
    refreshDisplayList();
}

async function refreshDisplayList() {
    const btn = document.getElementById('displaySelectText');
    btn.innerText = '正在获取屏幕...';
    
    try {
        if (!currentDevice) return;
        console.log("Fetching displays for:", currentDevice.serial);
        const res = await fetch(`/api/displays?serial=${currentDevice.serial}`);
        displaysList = await res.json();
        console.log("Displays received:", displaysList);
        
        if (displaysList.length > 0) {
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
            await refreshDeviceList();
            
            console.log(`[ConnectDevice] 已切换到新serial: ${targetSerial}`);
        } else {
            console.log("[ConnectDevice] 普通设备，无需初始化");
            addLogEntry(`✅ 普通Android设备，直接连接`, 'info');
        }
        
        // Step 2: Refresh display list
        console.log("[ConnectDevice] 刷新显示列表...");
        addLogEntry(`🖥️ 检测显示屏幕...`, 'info');
        await refreshDisplayList();

        // Step 3: Connect to the device
        console.log(`[ConnectDevice] 连接到设备: ${targetSerial}`);
        addLogEntry(`🔌 正在建立连接...`, 'info');
        
        const res = await fetch('/api/connect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ serial: targetSerial })
        });
        console.log(`[ConnectDevice] 连接API响应状态: ${res.status}`);
        
        if (!res.ok) {
            const errorText = await res.text();
            console.error(`[ConnectDevice] 连接失败: ${errorText}`);
            addLogEntry(`❌ 连接失败: ${errorText}`, 'error');
            throw new Error(errorText);
        }
        
        const data = await res.json();
        console.log("[ConnectDevice] 连接成功，设备信息:", data);
        const productName = data.info.productName || "Unknown Device";
        const statusEl = document.getElementById('status');
        statusEl.innerText = `已连接: ${productName}`;
        statusEl.classList.remove('status-badge');
        statusEl.style.color = '#10b981';
        statusEl.style.fontWeight = 'bold';
        
        addLogEntry(`✅ 连接成功: ${productName}`, 'success');
        
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

async function refreshSnapshot(forceShowLoading = true) {
    if (forceShowLoading) loading.classList.remove('hidden');
    try {
        // Parallel refresh
        await Promise.all([refreshScreen(), refreshHierarchy()]);
    } finally {
        if (forceShowLoading) loading.classList.add('hidden');
    }
}

function refreshScreen() {
    return new Promise((resolve) => {
        const displayId = currentDisplay || "0";
        const img = new Image();
        img.src = `/api/screenshot?display=${displayId}&t=${new Date().getTime()}`;
        img.onload = async () => {
            try {
                // 开启异步解码，避免主线程卡顿，实现 scrcpy 般的流畅感
                if (img.decode) await img.decode();
                screenImage = img;

                const hqScale = 2;
                canvas.width = screenImage.naturalWidth * hqScale;
                canvas.height = screenImage.naturalHeight * hqScale;

                ctx.imageSmoothingEnabled = true;
                ctx.imageSmoothingQuality = 'high';
                drawScreen();
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

function drawScreen() {
    if (!screenImage.src) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // 渲染截图 (填满整个 2x Canvas)
    ctx.drawImage(screenImage, 0, 0, canvas.width, canvas.height);

    // 绘制 UI 高亮 (需要缩放到 2x 坐标空间)
    ctx.save();
    ctx.scale(2, 2);

    // Draw Hover
    if (hoverNode && hoverNode !== selectedNode) {
        drawHighlight(hoverNode, '#3b82f6', 'rgba(59, 130, 246, 0.1)');
    }

    // Draw Selected
    if (selectedNode) {
        drawHighlight(selectedNode, '#ef4444', 'rgba(239, 68, 68, 0.2)');
    }

    ctx.restore();
}

async function refreshHierarchy() {
    try {
        const displayId = currentDisplay || "0";
        const res = await fetch(`/api/hierarchy?display=${displayId}`);
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

    const children = Array.from(xmlNode.children).filter(c => c.tagName === 'node');

    // Toggle Icon
    const toggle = document.createElement('span');
    toggle.className = 'toggle-btn';

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
    textSpan.innerText = label;
    content.appendChild(textSpan);

    content.onclick = (e) => {
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

    container.appendChild(content);

    if (children.length > 0) {
        const childContainer = document.createElement('div');
        childContainer.className = 'children-container';
        childContainer.style.display = 'none'; // Default Hidden
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
    for (const key of sortedKeys) {
        html += `<tr><th>${key}</th><td>${attrs[key]}</td></tr>`;
    }
    table.innerHTML = html;
    propsContainer.appendChild(table);
}

function drawHighlight(xmlNode, strokeColor = '#ef4444', fillColor = 'rgba(239, 68, 68, 0.2)') {
    const attrs = getAttributes(xmlNode);
    if (!attrs['bounds']) return;
    const b = parseBounds(attrs['bounds']);
    if (!b) return;

    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = 2;
    ctx.strokeRect(b.x, b.y, b.w, b.h);

    ctx.fillStyle = fillColor;
    ctx.fillRect(b.x, b.y, b.w, b.h);
}

// Interaction Variables
let isDragging = false;
let startX = 0;
let startY = 0;
let dragThreshold = 10; // Pixels to consider as drag
let dragStartTime = 0;

function getCanvasCoords(e) {
    const rect = canvas.getBoundingClientRect();

    // 关键修正：点击坐标必须映射回物理设备的原始分辨率 (1x)，而不是 Canvas 的 2x 采样分辨率
    const scaleX = screenImage.naturalWidth / rect.width;
    const scaleY = screenImage.naturalHeight / rect.height;

    return {
        x: (e.clientX - rect.left) * scaleX,
        y: (e.clientY - rect.top) * scaleY,
        rawX: (e.clientX - rect.left) * scaleX,
        rawY: (e.clientY - rect.top) * scaleY
    };
}

canvas.onmousedown = (e) => {
    // Only handle left click (0) for dragging
    if (e.button !== 0) return;
    isDragging = true;
    const coords = getCanvasCoords(e);
    startX = coords.rawX;
    startY = coords.rawY;
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
    if (!isDragging) return;
    isDragging = false;

    const coords = getCanvasCoords(e);
    const endX = coords.rawX;
    const endY = coords.rawY;
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
    // 1. Real Control Logic
    if (isRealControl) {
        performRealClick(x, y);
    }

    // 2. Inspection Logic (Always inspect on click)
    if (rootNode) {
        const allHits = findAllNodesAt(rootNode, x, y);
        const bestNode = pickBestNode(allHits);

        if (bestNode) {
            selectNode(bestNode);
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
        // Optional: Trigger refresh after a delay?
        if (document.getElementById('autoRefresh').checked) {
            // Screen will auto refresh soon
        } else {
            setTimeout(refreshScreen, 100); // Trigger a refresh after click
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
