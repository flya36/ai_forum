/* 帖子详情页实时脚本（post_detail.html，CSP 收紧后外部化）
 * SocketIO 房间加入/离开、新评论插入、点赞/收藏实时更新、评论表单 AJAX、
 * 删除表单确认、举报弹窗绑定。
 * 注意：本脚本位于 content block 内、base.html 的 bootstrap/site-base 脚本
 *       【之前】加载，因此 bootstrap / window.forumSocket 在脚本首次执行时
 *       尚未就绪——必须等 DOMContentLoaded（此时所有同步脚本已执行完）再初始化。
 * POST_ID 通过 <div id="post-page-meta" data-post-id="..."> 注入。
 */
(function () {
    'use strict';

    function init() {
        var metaEl = document.getElementById('post-page-meta');
        if (!metaEl) return;
        var POST_ID = parseInt(metaEl.getAttribute('data-post-id') || '0', 10);
        if (!POST_ID) return;

        /* ===== 删除表单确认（原 onsubmit 内联 confirm 外部化） ===== */
        document.querySelectorAll('form.confirm-delete').forEach(function (form) {
            form.addEventListener('submit', function (e) {
                var msg = form.getAttribute('data-confirm-msg') || '确定删除？';
                if (!window.confirm(msg)) e.preventDefault();
            });
        });

        /* ===== 举报弹窗：点击"举报"按钮打开共用 modal，动态写入目标类型/ID =====
         * 使用 document 事件委托：服务端渲染 + SocketIO 动态插入的评论均可触发 */
        var reportModalEl = document.getElementById('report-modal');
        var reportForm = document.getElementById('report-form');
        if (reportModalEl && reportForm && typeof bootstrap !== 'undefined') {
            document.addEventListener('click', function (e) {
                var btn = e.target.closest ? e.target.closest('.btn-report-open') : null;
                if (!btn) return;
                reportForm.elements['target_type'].value = btn.getAttribute('data-target-type') || '';
                reportForm.elements['target_id'].value = btn.getAttribute('data-target-id') || '';
                var desc = reportForm.querySelector('textarea[name="description"]');
                if (desc) desc.value = '';
                bootstrap.Modal.getOrCreateInstance(reportModalEl).show();
            });
        }

        /* ===== 评论点赞：事件委托（服务端渲染 + 动态插入评论均生效） =====
         * 乐观更新：点击立即切换 UI，请求失败回滚。
         * 只更新点击者自己的按钮状态；其他在线用户通过 comment_like_update 更新计数。 */
        function applyCommentLikeUI(btn, liked, count) {
            var icon = btn.querySelector('i');
            var cntEl = btn.querySelector('.comment-like-cnt');
            if (icon) icon.className = liked ? 'bi bi-heart-fill text-danger' : 'bi bi-heart';
            if (cntEl && typeof count === 'number') cntEl.textContent = count;
            btn.setAttribute('data-liked', liked ? '1' : '0');
            btn.setAttribute('title', liked ? '取消点赞' : '点赞');
        }
        document.addEventListener('click', function (e) {
            var btn = e.target.closest ? e.target.closest('.comment-like-btn') : null;
            if (!btn || !window.fetch) return;
            var cid = btn.getAttribute('data-comment-id');
            if (!cid) return;
            var prevLiked = btn.getAttribute('data-liked') === '1';
            var cntEl = btn.querySelector('.comment-like-cnt');
            var prevCount = cntEl ? (parseInt(cntEl.textContent, 10) || 0) : 0;
            var optLiked = !prevLiked;
            applyCommentLikeUI(btn, optLiked, prevCount + (optLiked ? 1 : -1));
            var fd = new FormData();
            fd.append('_csrf_token', window.CSRF_TOKEN || '');
            fetch('/api/comment/' + cid + '/like', {
                method: 'POST',
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                body: fd
            }).then(function (r) {
                return r.json().then(function (d) { return { status: r.status, data: d }; });
            }).then(function (res) {
                var d = res.data || {};
                if (res.status === 200 && d.ok) {
                    applyCommentLikeUI(btn, d.liked, d.count);
                } else {
                    applyCommentLikeUI(btn, prevLiked, prevCount);  // 回滚
                    if (d.message) alert(d.message);
                }
            }).catch(function () {
                applyCommentLikeUI(btn, prevLiked, prevCount);  // 回滚
            });
        });

        /* ===== 分享弹窗：好友按需分页拉取（POST /api/friends/list 带 CSRF）+ AJAX 提交 ===== */
        var shareModalEl = document.getElementById('share-modal');
        var shareForm = document.getElementById('share-form');
        if (shareModalEl && shareForm && typeof bootstrap !== 'undefined') {
            var shareFriendsList = document.getElementById('share-friends-list');
            var shareLoading = document.getElementById('share-friends-loading');
            var shareMoreBtn = document.getElementById('share-friends-more');
            var shareFriendsPage = 1;
            var shareFriendsTotal = 0;
            var shareFriendsLoaded = 0;

            function csrfFromForm() {
                var el = shareForm.querySelector('input[name="csrf_token"]');
                return el ? el.value : '';
            }

            function renderFriendRow(f) {
                var label = document.createElement('label');
                label.className = 'd-flex align-items-center py-2 border-bottom';
                label.style.cursor = 'pointer';
                var radio = document.createElement('input');
                radio.type = 'radio';
                radio.name = 'receiver_id';
                radio.value = String(f.user_id);
                radio.className = 'me-2';
                radio.required = true;
                label.appendChild(radio);
                if (f.avatar_path) {
                    var img = document.createElement('img');
                    img.src = '/uploads/avatars/' + encodeURIComponent(f.avatar_path);
                    img.className = 'rounded-circle me-2';
                    img.style.cssText = 'width:32px;height:32px;object-fit:cover;';
                    label.appendChild(img);
                } else {
                    var ph = document.createElement('div');
                    ph.className = 'rounded-circle bg-secondary text-white d-flex align-items-center justify-content-center me-2';
                    ph.style.cssText = 'width:32px;height:32px;';
                    ph.textContent = (f.nickname || '?').charAt(0);
                    label.appendChild(ph);
                }
                var span = document.createElement('span');
                span.textContent = f.nickname || f.username;   // 文本节点，防止 XSS
                label.appendChild(span);
                return label;
            }

            function loadShareFriends(page) {
                if (!shareFriendsList || !shareLoading) return Promise.resolve();
                if (page === 1) {
                    shareFriendsList.innerHTML = '';
                    shareLoading.style.display = 'block';
                }
                var fd = new FormData();
                fd.append('_csrf_token', csrfFromForm());
                fd.append('page', String(page));
                fd.append('page_size', '20');
                return fetch('/api/friends/list', {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                    body: fd
                }).then(function (r) { return r.json(); }).then(function (d) {
                    if (!d || !d.ok) {
                        if (page === 1) {
                            shareFriendsList.innerHTML = '';
                            var empty = document.createElement('p');
                            empty.className = 'text-muted text-center py-3';
                            empty.textContent = '暂无好友，先去用户主页互相关注吧';
                            shareFriendsList.appendChild(empty);
                        }
                        return;
                    }
                    shareLoading.style.display = 'none';
                    shareFriendsTotal = d.total || 0;
                    (d.friends || []).forEach(function (f) { shareFriendsList.appendChild(renderFriendRow(f)); });
                    shareFriendsLoaded += (d.friends || []).length;
                    var hasMore = shareFriendsLoaded < shareFriendsTotal;
                    if (shareMoreBtn) shareMoreBtn.classList.toggle('d-none', !hasMore);
                    if (shareFriendsLoaded === 0) {
                        var empty = document.createElement('p');
                        empty.className = 'text-muted text-center py-3';
                        empty.textContent = '暂无好友，先去用户主页互相关注吧';
                        shareFriendsList.appendChild(empty);
                    } else {
                        var radios = shareFriendsList.querySelectorAll('input[name="receiver_id"]');
                        if (radios.length > 0 && !shareFriendsList.querySelector('input:checked')) radios[0].checked = true;
                    }
                }).catch(function () {
                    shareLoading.style.display = 'none';
                    shareFriendsList.innerHTML = '';
                    var err = document.createElement('p');
                    err.className = 'text-muted text-center py-3';
                    err.textContent = '好友列表加载失败，请稍后再试';
                    shareFriendsList.appendChild(err);
                });
            }

            document.querySelectorAll('.btn-share-open').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    var pid = btn.getAttribute('data-post-id') || '';
                    shareForm.elements['post_id'].value = pid;
                    shareFriendsPage = 1;
                    shareFriendsLoaded = 0;
                    bootstrap.Modal.getOrCreateInstance(shareModalEl).show();
                    loadShareFriends(1);
                });
            });
            if (shareMoreBtn) {
                shareMoreBtn.addEventListener('click', function () {
                    shareFriendsPage += 1;
                    loadShareFriends(shareFriendsPage);
                });
            }
            shareForm.addEventListener('submit', function (e) {
                e.preventDefault();
                var submitBtn = document.getElementById('share-submit');
                if (submitBtn) submitBtn.disabled = true;
                fetch('/api/message/share', {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                    body: new FormData(shareForm)
                }).then(function (r) {
                    return r.json().then(function (d) { return { status: r.status, data: d }; });
                }).then(function (res) {
                    var d = res.data || {};
                    if (res.status === 200 && d.ok) {
                        bootstrap.Modal.getOrCreateInstance(shareModalEl).hide();
                        alert('分享成功');
                    } else {
                        alert(d.msg || '分享失败，请稍后再试');
                    }
                }).catch(function () {
                    alert('分享失败，请稍后再试');
                }).finally(function () {
                    if (submitBtn) submitBtn.disabled = false;
                });
            });
        }

        var socket = window.forumSocket;
        if (!socket) return;   // 实时通道不可用时页面功能不受影响，评论仍走表单提交

        socket.emit('join_post', { post_id: POST_ID });
        window.addEventListener('beforeunload', function () {
            socket.emit('leave_post', { post_id: POST_ID });
        });

        socket.on('new_comment', function (c) { insertComment(c); });

        socket.on('like_update', function (data) {
            if (!data || data.post_id !== POST_ID) return;
            var countEl = document.getElementById('like-count');
            if (countEl && typeof data.like_count === 'number') countEl.textContent = data.like_count;
        });
        socket.on('comment_like_update', function (data) {
            if (!data || data.post_id !== POST_ID) return;
            var btn = document.querySelector('.comment-like-btn[data-comment-id="' + data.comment_id + '"]');
            if (btn && typeof data.like_count === 'number') {
                var cntEl = btn.querySelector('.comment-like-cnt');
                if (cntEl) cntEl.textContent = data.like_count;
                // 仅更新计数，不覆盖 data-liked（不篡改其他用户的已赞状态）
            }
        });
        socket.on('favorite_update', function (data) {
            if (!data || data.post_id !== POST_ID) return;
            var countEl = document.getElementById('fav-count');
            if (countEl && typeof data.favorite_count === 'number') countEl.textContent = data.favorite_count;
        });

        function bindActionForm(formId, onSuccess) {
            var form = document.getElementById(formId);
            if (!form || !window.fetch) return;
            form.addEventListener('submit', function (e) {
                e.preventDefault();
                fetch(form.action, {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                    body: new FormData(form)
                }).then(function (r) { return r.json(); }).then(function (data) {
                    if (data && data.ok) onSuccess(data);
                    else location.reload();
                }).catch(function () { form.submit(); });
            });
        }
        bindActionForm('like-form', function (d) {
            var icon = document.getElementById('like-icon');
            var text = document.getElementById('like-text');
            var count = document.getElementById('like-count');
            if (icon) icon.className = d.liked ? 'bi bi-hand-thumbs-up-fill text-primary' : 'bi bi-hand-thumbs-up';
            if (text) text.textContent = d.liked ? '已点赞' : '点赞';
            if (count && typeof d.like_count === 'number') count.textContent = d.like_count;
        });
        bindActionForm('favorite-form', function (d) {
            var icon = document.getElementById('fav-icon');
            var text = document.getElementById('fav-text');
            var count = document.getElementById('fav-count');
            if (icon) icon.className = d.favorited ? 'bi bi-star-fill text-warning' : 'bi bi-star';
            if (text) text.textContent = d.favorited ? '已收藏' : '收藏';
            if (count && typeof d.favorite_count === 'number') count.textContent = d.favorite_count;
        });

        socket.on('delete_post', function (data) {
            if (data.post_id === POST_ID) {
                alert('该帖子已被删除');
                window.location.href = '/';
            }
        });

        socket.on('delete_comment', function (data) {
            var el = document.querySelector('[data-comment-id="' + data.comment_id + '"]');
            if (el) {
                el.remove();
                setCommentCount(getCommentCount() - 1);
            }
        });

        function getCommentCount() {
            var el = document.getElementById('comment-count');
            return el ? (parseInt(el.textContent, 10) || 0) : 0;
        }
        function setCommentCount(n) {
            var el = document.getElementById('comment-count');
            if (el && n >= 0) el.textContent = n;
        }

        function buildCommentItem(c) {
            var item = document.createElement('div');
            item.className = 'comment-item d-flex';
            item.id = 'comment-' + c.comment_id;  // 锚点：通知跳转 /post/<id>#comment-<id>
            item.setAttribute('data-comment-id', c.comment_id);

            var avatar;
            if (c.avatar_path) {
                avatar = document.createElement('img');
                avatar.className = 'avatar-sm me-3';
                avatar.src = '/uploads/avatars/' + encodeURIComponent(c.avatar_path);
            } else {
                avatar = document.createElement('div');
                avatar.className = 'avatar-sm me-3 d-flex align-items-center justify-content-center bg-secondary text-white rounded-circle';
                avatar.textContent = (c.nickname || '?').charAt(0);
            }

            var right = document.createElement('div');
            var meta = document.createElement('div');
            meta.className = 'small';
            var floor = document.createElement('span');
            floor.className = 'text-muted';
            floor.textContent = c.floor + '楼';
            var link = document.createElement('a');
            link.className = 'fw-bold text-decoration-none ms-1';
            link.href = '/user/' + c.user_id;
            link.textContent = c.nickname;
            var time = document.createElement('span');
            time.className = 'text-muted ms-2';
            time.textContent = c.create_time;
            meta.appendChild(floor);
            meta.appendChild(link);
            meta.appendChild(time);

            var body = document.createElement('div');
            body.className = 'mt-1 markdown-body';
            // content_html 为服务端 bleach 白名单过滤后的安全 HTML；旧数据回退 textContent
            if (c.content_html) {
                body.innerHTML = c.content_html;
            } else {
                body.style.whiteSpace = 'pre-wrap';
                body.textContent = c.content;
            }

            // 两端对齐操作行：左=点赞，右=举报（与帖子详情页服务端渲染一致）
            var actions = document.createElement('div');
            actions.className = 'd-flex justify-content-between align-items-center mt-1';
            var likeBtn = document.createElement('button');
            likeBtn.type = 'button';
            likeBtn.className = 'btn btn-sm btn-link p-0 text-decoration-none comment-like-btn';
            likeBtn.setAttribute('data-comment-id', c.comment_id);
            likeBtn.setAttribute('data-liked', c.liked ? '1' : '0');
            likeBtn.setAttribute('data-count', c.like_count || 0);
            likeBtn.setAttribute('title', c.liked ? '取消点赞' : '点赞');
            var likeIcon = document.createElement('i');
            likeIcon.className = c.liked ? 'bi bi-heart-fill text-danger' : 'bi bi-heart';
            likeBtn.appendChild(likeIcon);
            var likeCnt = document.createElement('span');
            likeCnt.className = 'comment-like-cnt ms-1';
            likeCnt.textContent = c.like_count || 0;
            likeBtn.appendChild(likeCnt);
            actions.appendChild(likeBtn);
            var reportBtn = document.createElement('button');
            reportBtn.type = 'button';
            reportBtn.className = 'btn btn-sm btn-link text-muted p-0 small btn-report-open';
            reportBtn.setAttribute('data-target-type', 'comment');
            reportBtn.setAttribute('data-target-id', c.comment_id);
            reportBtn.setAttribute('title', '举报');
            var flagIcon = document.createElement('i');
            flagIcon.className = 'bi bi-flag';
            reportBtn.appendChild(flagIcon);
            reportBtn.appendChild(document.createTextNode(' 举报'));
            actions.appendChild(reportBtn);

            right.appendChild(meta);
            right.appendChild(body);
            right.appendChild(actions);
            item.appendChild(avatar);
            item.appendChild(right);
            return item;
        }

        /* ===== Markdown 图片增强：懒加载 + 点击查看原图（新窗口） ===== */
        function enhanceMarkdownImages(root) {
            if (!root) return;
            var images = root.querySelectorAll('.markdown-body img');
            for (var i = 0; i < images.length; i++) {
                var im = images[i];
                if (!im.loading) im.loading = 'lazy';
                if (im.closest && im.closest('a')) continue;   // 已有外层链接则不动
                var src = im.getAttribute('src');
                if (!src) continue;
                var a = document.createElement('a');
                a.href = src;
                a.target = '_blank';
                a.rel = 'noopener';
                im.parentNode.insertBefore(a, im);
                a.appendChild(im);
            }
        }
        enhanceMarkdownImages(document.body);   // 正文 + 已渲染评论

        function insertComment(c) {
            if (!c || document.querySelector('[data-comment-id="' + c.comment_id + '"]')) return;
            var empty = document.getElementById('comment-empty');
            if (empty) empty.remove();
            var list = document.getElementById('comment-list');
            if (!list) return;
            var item = buildCommentItem(c);
            list.appendChild(item);
            enhanceMarkdownImages(item);   // 动态评论内的 markdown 图片：懒加载 + 点击看原图
            item.classList.add('comment-flash');
            setTimeout(function () { item.classList.remove('comment-flash'); }, 2000);
            setCommentCount(getCommentCount() + 1);
            item.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }

        var form = document.getElementById('comment-form');
        if (form && window.fetch) {
            form.addEventListener('submit', function (e) {
                var textarea = form.querySelector('textarea[name="content"]');
                if (!textarea || !textarea.value.trim()) { e.preventDefault(); return; }
                e.preventDefault();
                fetch(form.action, {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                    body: new FormData(form)
                }).then(function (r) { return r.json(); }).then(function (data) {
                    if (data && data.ok && data.comment) {
                        textarea.value = '';
                        insertComment(data.comment);
                    } else if (data && data.ok) {
                        location.reload();
                    } else if (data && data.message) {
                        alert(data.message);
                    } else {
                        location.reload();
                    }
                }).catch(function () {
                    form.submit();
                });
            });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
