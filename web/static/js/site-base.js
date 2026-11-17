/* 站点基础脚本（CSP 收紧后外部化自 base.html 内联块）
 * 职责：CSRF 自动注入 / SocketIO 全局初始化 / 通知角标与提示 / 搜索建议与回车跳转
 * 约定：页面通过 <meta name="csrf-token"> 注入 CSRF；<body data-user-id> 标识登录态。
 */
(function () {
    'use strict';

    /* ===== CSRF 令牌（meta 注入，CSP 下无需内联 script） ===== */
    var meta = document.querySelector('meta[name="csrf-token"]');
    window.CSRF_TOKEN = meta ? (meta.getAttribute('content') || '') : '';
    var token = window.CSRF_TOKEN || '';
    if (token) {
        document.addEventListener('submit', function (e) {
            var f = e.target;
            if (f && f.tagName === 'FORM' && f.method && f.method.toUpperCase() === 'POST') {
                if (!f.querySelector('input[name="_csrf_token"]')) {
                    var inp = document.createElement('input');
                    inp.type = 'hidden';
                    inp.name = '_csrf_token';
                    inp.value = token;
                    f.appendChild(inp);
                }
            }
        }, true);
    }

    /* ===== SocketIO 全局初始化：仅登录用户建立连接 ===== */
    window.forumSocket = null;
    window.showNetTip = function (msg, isWarn) {
        var tip = document.getElementById('net-tip');
        if (!tip) {
            tip = document.createElement('div');
            tip.id = 'net-tip';
            tip.className = 'net-tip';
            document.body.appendChild(tip);
        }
        tip.style.background = isWarn ? '#b3541e' : '#2e7d32';
        tip.textContent = msg;
        tip.style.display = 'block';
        clearTimeout(tip._timer);
        tip._timer = setTimeout(function () { tip.style.display = 'none'; }, 3000);
    };
    window.showNotifyToast = function (content) {
        var tip = document.getElementById('net-tip');
        if (!tip) {
            tip = document.createElement('div');
            tip.id = 'net-tip';
            tip.className = 'net-tip';
            document.body.appendChild(tip);
        }
        tip.style.background = '#4E6EF2';
        tip.textContent = content;
        tip.style.display = 'block';
        tip.style.cursor = 'pointer';
        tip.onclick = function () { window.location.href = '/notifications'; };
        clearTimeout(tip._timer);
        tip._timer = setTimeout(function () { tip.style.display = 'none'; }, 5000);
    };
    // 通知未读：更新"系统通知"Tab 徽标（顶部铃铛已移除，通知入口集中于消息中心/侧边栏）
    window.updateUnreadBadge = function (n) {
        var badges = document.querySelectorAll('.msg-notice-badge');
        for (var i = 0; i < badges.length; i++) {
            var old = badges[i];
            if (n > 0) { old.textContent = n; old.style.display = ''; }
            else { old.style.display = 'none'; }
        }
    };

    // 私信/消息中心总未读：页面加载时从 body data 属性读取（服务端注入）
    window._msgUnread = parseInt(document.body.getAttribute('data-msg-unread') || '0', 10) || 0;
    window._msgTotal = parseInt(document.body.getAttribute('data-msg-total') || '0', 10) || 0;
    window.updateMsgUnreadBadge = function (n) {
        window._msgUnread = n;
        var badges = document.querySelectorAll('.msg-unread-badge');
        for (var i = 0; i < badges.length; i++) {
            var old = badges[i];
            if (n > 0) { old.textContent = n; old.style.display = ''; }
            else { old.style.display = 'none'; }
        }
    };
    // 消息中心总未读（私信 + 通知）：更新侧边栏/下拉"我的消息"红点
    window.updateMsgTotalBadge = function (n) {
        window._msgTotal = n;
        var badges = document.querySelectorAll('.msg-total-badge');
        for (var i = 0; i < badges.length; i++) {
            var old = badges[i];
            if (n > 0) { old.textContent = n; old.style.display = ''; }
            else { old.style.display = 'none'; }
        }
    };

    var currentUserId = document.body.getAttribute('data-user-id') || '';
    if (currentUserId && typeof io === 'function') {
        var hadError = false;
        // withCredentials: true —— 关键修复：socket.io v4 默认不携带会话 Cookie，
        // 导致服务端 connect 鉴权失败被拒 → 客户端无限自动重连（秒断秒连死循环）。
        window.forumSocket = io({ withCredentials: true });
        forumSocket.on('connect', function () {
            if (hadError) { showNetTip('实时更新已恢复', false); hadError = false; }
        });
        forumSocket.on('disconnect', function () {
            hadError = true;
            showNetTip('实时连接已断开，正在自动重连...', true);
        });
        forumSocket.on('connect_error', function () {
            hadError = true;
            showNetTip('实时连接不可用，页面功能不受影响', true);
        });
        // 服务端告知：登录态失效（cookie 丢失/过期）时主动断开，避免空连占用
        forumSocket.on('auth_expired', function () {
            hadError = true;
            showNetTip('登录状态已失效，请重新登录', true);
            if (forumSocket.connected) forumSocket.disconnect();
        });
        forumSocket.on('notification', function (data) {
            if (!data) return;
            // 私信/分享类型已由 new_message 事件处理：此处忽略，避免系统通知红点被污染
            if (data.type === 'message' || data.type === 'share') return;
            if (typeof data.unread === 'number') {
                window.updateUnreadBadge(data.unread);
                // 通知到达：总未读 = 最新通知未读 + 当前私信未读
                window.updateMsgTotalBadge(data.unread + window._msgUnread);
            }
            if (data.content) showNotifyToast(data.content);
        });
        // 私信/分享实时到达：私信未读 +1、消息中心总未读 +1 + Toast（点击跳私信对话）
        forumSocket.on('new_message', function (data) {
            if (!data) return;
            window.updateMsgUnreadBadge(window._msgUnread + 1);
            window.updateMsgTotalBadge(window._msgTotal + 1);
            if (data.from_nickname) {
                var tip = document.getElementById('net-tip');
                if (!tip) {
                    tip = document.createElement('div');
                    tip.id = 'net-tip';
                    tip.className = 'net-tip';
                    document.body.appendChild(tip);
                }
                tip.style.background = '#2E7D32';
                tip.textContent = (data.post_id ? data.from_nickname + ' 给你分享了一篇帖子' : data.from_nickname + ' 给你发来一条私信');
                tip.style.display = 'block';
                tip.style.cursor = 'pointer';
                tip.onclick = function () { window.location.href = '/messages/' + (data.from_user_id || ''); };
                clearTimeout(tip._timer);
                tip._timer = setTimeout(function () { tip.style.display = 'none'; }, 5000);
            }
        });
        // 新工单实时提醒（admins 房间广播，仅管理员收到）：总未读 +1 + Toast，点击跳审核队列
        forumSocket.on('new_ticket_alert', function (data) {
            if (!data) return;
            showNotifyToast(data.message || '有新的审核工单待处理');
            var tip = document.getElementById('net-tip');
            if (tip) tip.onclick = function () { window.location.href = '/admin?tab=reports'; };
            window.updateMsgTotalBadge(window._msgTotal + 1);
        });
        forumSocket.on('delete_post', function (data) {
            var el = document.querySelector('[data-post-card="' + data.post_id + '"]');
            if (el) el.remove();
        });
    }

    /* ===== 搜索建议与搜索 ===== */
    var suggestTimer = null;
    window.doSearch = function () {
        var q = document.getElementById('search-input');
        if (q && q.value.trim()) window.location.href = '/search?q=' + encodeURIComponent(q.value.trim());
    };
    window.doSuggest = function (q) {
        var dd = document.getElementById('search-dropdown');
        if (!dd) return;
        clearTimeout(suggestTimer);
        q = (q || '').trim();
        if (!q) { dd.style.display = 'none'; return; }
        suggestTimer = setTimeout(function () {
            fetch('/api/search?q=' + encodeURIComponent(q))
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (!data.results || data.results.length === 0) {
                        dd.style.display = 'none';
                        return;
                    }
                    dd.innerHTML = '';
                    data.results.forEach(function (r) {
                        var a = document.createElement('a');
                        a.href = '/post/' + encodeURIComponent(r.post_id);
                        a.className = 'list-group-item list-group-item-action py-2 px-3';
                        a.textContent = r.title;   // textContent 防注入
                        dd.appendChild(a);
                    });
                    dd.style.display = 'block';
                });
        }, 200);
    };

    var searchInput = document.getElementById('search-input');
    if (searchInput) {
        searchInput.addEventListener('input', function () {
            window.doSuggest(searchInput.value);
        });
        searchInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                window.doSearch();
            }
        });
        var desktopSearchBtn = document.getElementById('search-btn-desktop');
        if (desktopSearchBtn) {
            desktopSearchBtn.addEventListener('click', function () {
                window.doSearch();
            });
        }
        document.addEventListener('click', function (e) {
            var dd = document.getElementById('search-dropdown');
            if (dd && !e.target.closest('#search-input') && !e.target.closest('#search-dropdown')) {
                dd.style.display = 'none';
            }
        });
    }

    /* ===== 移动端侧边栏抽屉搜索框（汉堡菜单内） ===== */
    var mobileSearch = document.getElementById('search-input-mobile');
    var mobileSearchBtn = document.getElementById('search-btn-mobile');
    function doMobileSearch() {
        if (mobileSearch && mobileSearch.value.trim()) {
            window.location.href = '/search?q=' + encodeURIComponent(mobileSearch.value.trim());
        }
    }
    if (mobileSearch) {
        mobileSearch.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                doMobileSearch();
            }
        });
    }
    if (mobileSearchBtn) {
        mobileSearchBtn.addEventListener('click', doMobileSearch);
    }
})();
