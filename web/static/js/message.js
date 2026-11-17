// 私信对话页：发送（CSRF） + SocketIO 实时接收 + 自动滚底
(function () {
    'use strict';
    function esc(s) {
        var d = document.createElement('div');
        d.textContent = s == null ? '' : String(s);
        return d.innerHTML;
    }
    function timeStr() {
        var d = new Date();
        function p(n) { return n < 10 ? '0' + n : '' + n; }
        return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
    }
    // 头像 HTML（URL 由服务端模板注入，JS 不自行拼接；无头像用昵称首字圆形占位）
    function avatarHtml(url, name, uid, align) {
        var href = '/user/' + encodeURIComponent(uid);
        var wrap = align === 'left'
            ? '<a class="text-decoration-none me-2" href="' + href + '">'
            : '<a class="text-decoration-none" href="' + href + '">';
        var inner = url
            ? '<img src="' + esc(url) + '" class="rounded-circle" style="width:36px;height:36px;object-fit:cover;" alt="' + esc(name) + '">'
            : '<div class="rounded-circle bg-secondary text-white d-flex align-items-center justify-content-center" style="width:36px;height:36px;">' + esc(name[0] || '?') + '</div>';
        return wrap + inner + '</a>';
    }
    function scrollBottom() {
        var box = document.getElementById('thread-box');
        if (box) box.scrollTop = box.scrollHeight;
    }
    scrollBottom();
    var form = document.getElementById('msg-form');
    if (!form) return;
    var input = document.getElementById('msg-input');
    var otherId = form.getAttribute('data-other-id');
    var csrf = form.querySelector('input[name=csrf_token]').value;
    var box = document.getElementById('thread-box');
    var myId = form.getAttribute('data-my-id');
    var myName = form.getAttribute('data-my-name') || '';
    var myAvatarUrl = form.getAttribute('data-my-avatar-url') || '';
    var otherName = form.getAttribute('data-other-name') || '';
    var otherAvatarUrl = form.getAttribute('data-other-avatar-url') || '';
        form.addEventListener('submit', function (ev) {
            ev.preventDefault();
            var content = input.value.trim();
            if (!content) return;
            var fd = new FormData();
            fd.append('_csrf_token', csrf);
            fd.append('receiver_id', otherId);
            fd.append('content', content);
            fetch('/api/messages/send', { method: 'POST', body: fd, credentials: 'same-origin' })
                .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
                .then(function (res) {
                    if (res.j && res.j.ok) {
                        input.value = '';
                        // 发送方立即显示自己的消息（乐观渲染）：右侧自己头像 + 气泡
                        var wrap = document.createElement('div');
                        wrap.className = 'd-flex justify-content-end align-items-start mb-2';
                        wrap.innerHTML = '<div class="msg-bubble bg-primary text-white me-2" style="max-width:65%;padding:8px 12px;border-radius:12px;">' +
                            '<div class="markdown-body small">' + esc(content) + '</div>' +
                            '<div class="small opacity-75 mt-1" style="font-size:11px;">' + timeStr() + '</div></div>' +
                            avatarHtml(myAvatarUrl, myName, myId, 'right');
                        box.appendChild(wrap);
                        scrollBottom();
                    } else {
                        alert((res.j && res.j.msg) || '发送失败');
                    }
                })
                .catch(function () { alert('网络错误，发送失败'); });
        });
        // SocketIO 实时接收对方消息：左侧对方头像 + 气泡
        if (window.forumSocket) {
            window.forumSocket.on('new_message', function (data) {
                if (!data || String(data.from_user_id) !== String(otherId)) return;
                var wrap = document.createElement('div');
                wrap.className = 'd-flex justify-content-start align-items-start mb-2';
                wrap.innerHTML = avatarHtml(otherAvatarUrl, otherName, otherId, 'left') +
                    '<div class="msg-bubble bg-light" style="max-width:65%;padding:8px 12px;border-radius:12px;">' +
                    '<div class="markdown-body small">' + esc(data.content) + '</div>' +
                    '<div class="small opacity-75 mt-1" style="font-size:11px;">' + (data.created_at || timeStr()) + '</div></div>';
                box.appendChild(wrap);
                scrollBottom();
            });
        }
})();
