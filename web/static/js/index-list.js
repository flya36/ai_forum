/* 首页/收藏页实时新帖推送（index.html，CSP 收紧后外部化）
 * 当前板块过滤视图通过 <div id="list-meta" data-board-id="..."> 注入（空=全部）。
 */
(function () {
    'use strict';
    var socket = window.forumSocket;
    if (!socket) return;   // 实时通道不可用时列表正常显示，手动刷新即可
    if (window.location.pathname === '/favorites') return;  // 收藏页不做新帖推送

    var meta = document.getElementById('list-meta');
    var currentBoardId = meta ? (meta.getAttribute('data-board-id') || '') : '';
    if (currentBoardId) currentBoardId = parseInt(currentBoardId, 10);
    var pendingPosts = [];

    socket.on('new_post', function (p) {
        if (!p || !p.post_id) return;
        if (currentBoardId && p.board_id !== currentBoardId) return;
        if (pendingPosts.some(function (x) { return x.post_id === p.post_id; })) return;  // 去重
        if (document.querySelector('[data-post-card="' + p.post_id + '"]')) return;      // 已在列表中
        pendingPosts.push(p);
        var banner = document.getElementById('new-post-banner');
        var count = document.getElementById('new-post-count');
        if (banner && count) {
            count.textContent = pendingPosts.length;
            banner.style.display = 'block';
        }
    });

    var banner = document.getElementById('new-post-banner');
    if (banner) {
        banner.addEventListener('click', function () {
            var area = document.getElementById('post-list-area');
            if (!area) return;
            var empty = document.getElementById('post-empty');
            if (empty) empty.remove();
            pendingPosts.forEach(function (p) {
                if (document.querySelector('[data-post-card="' + p.post_id + '"]')) return;
                area.insertBefore(buildPostCard(p), area.firstChild);
            });
            pendingPosts = [];
            banner.style.display = 'none';
        });
    }

    function buildPostCard(p) {
        var card = document.createElement('div');
        card.className = 'post-card';
        card.setAttribute('data-post-card', p.post_id);
        var inner = document.createElement('div');
        inner.className = 'd-flex justify-content-between align-items-start';
        var left = document.createElement('div');
        left.className = 'flex-grow-1';

        if (p.image_path) {
            var t = document.createElement('a');
            t.href = '/post/' + p.post_id;
            t.className = 'd-inline-block mb-1';
            var im = document.createElement('img');
            im.src = '/uploads/post_images/' + encodeURIComponent(p.image_path.split('.')[0] + '_thumb.jpg');
            im.alt = '';
            im.loading = 'lazy';
            im.className = 'rounded border';
            im.style.cssText = 'max-width:300px;max-height:180px;object-fit:cover;';
            t.appendChild(im);
            left.insertBefore(t, left.firstChild);
        }

        var titleLine = document.createElement('div');
        if (p.is_anonymous) {
            var anon = document.createElement('span');
            anon.className = 'tag-anon';
            anon.textContent = '匿名';
            titleLine.appendChild(anon);
        }
        var a = document.createElement('a');
        a.className = 'post-title';
        a.href = '/post/' + p.post_id;
        a.textContent = p.title;
        titleLine.appendChild(a);

        var metaLine = document.createElement('div');
        metaLine.className = 'post-meta mt-1';
        var iconUser = document.createElement('i');
        iconUser.className = 'bi bi-person-circle';
        metaLine.appendChild(iconUser);
        metaLine.appendChild(document.createTextNode(' ' + p.author + ' · ' + p.create_time + ' · '));
        var iconEye = document.createElement('i');
        iconEye.className = 'bi bi-eye';
        metaLine.appendChild(iconEye);
        metaLine.appendChild(document.createTextNode(' 0 · '));
        var iconChat = document.createElement('i');
        iconChat.className = 'bi bi-chat';
        metaLine.appendChild(iconChat);
        metaLine.appendChild(document.createTextNode(' 0'));

        left.appendChild(titleLine);
        left.appendChild(metaLine);
        inner.appendChild(left);
        card.appendChild(inner);
        return card;
    }
})();
