(() => {
    "use strict";
    const root = document.createElement("section");
    root.id = "recipes";
    root.hidden = true;
    root.setAttribute("aria-label", "菜谱");
    document.getElementById("people").after(root);
    const nav = document.createElement("button");
    nav.dataset.page = "recipes";
    nav.innerHTML = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M4 3v6c0 3 6 3 6 0V3M7 3v18M20 21V3c-5 1-6 10 0 10"/></svg><span>菜谱</span>';
    nav.onclick = () => showPage("recipes");
    document.querySelector('[data-page="health"]').after(nav);
    const dialog = document.createElement("dialog");
    dialog.id = "recipe-dialog";
    dialog.className = "recipe-dialog";
    document.body.append(dialog);
    const healthShortcut = document.createElement("div");
    healthShortcut.className = "recipe-health-shortcut";
    healthShortcut.innerHTML = '<button type="button">饮食与基础病资料 →</button><span>供已授权的家庭饮食分析参考</span>';
    document.querySelector("#health .health-hero")?.after(healthShortcut);
    healthShortcut.querySelector("button").onclick = async () => {
        try { state = await api(); healthEditor(); }
        catch (_) { open("暂时无法读取", "<p>健康资料暂时无法读取，请稍后再试。</p>"); }
    };
    let state = null, tab = "tonight", query = "", category = "全部", busy = false, loaded = false, draft = {
        members: [],
        guests: 2,
        count: 4,
        meal_type: "dinner",
        quick: false,
        date: localDay()
    };
    const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
    }[char]));
    const units = [ "g", "ml", "个", "根", "片", "瓣", "勺", "颗", "块", "把" ];
    const nutrients = [["carbs", "碳水化合物"], ["protein", "蛋白质"], ["fat", "脂肪"], ["fiber", "膳食纤维"]];
    const meals = {breakfast: "早餐", lunch: "午餐", dinner: "晚餐", other: "其他餐次"};
    const analysisAllowed = () => !["guest", "demo"].includes(window.tiangongAccount?.user?.role);
    let analysisTimer = null, analysisPolls = 0;
    function localDay() {
        const now = new Date;
        return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
    }
    function quantity(value) {
        return Number(Number(value).toFixed(1)).toLocaleString("zh-CN", {
            maximumFractionDigits: 1
        });
    }
    function amount(item, field = "quantity") {
        return item[field] === null ? "适量" : quantity(item[field]) + item.unit;
    }
    async function api(payload) {
        const response = await fetch("/api/recipes", payload ? {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                ...payload,
                revision: state?.revision
            })
        } : {
            cache: "no-store"
        });
        const result = await response.json();
        if (!response.ok) throw Error(result.error || "菜谱暂时无法加载");
        return result;
    }
    function message(value, error = false) {
        const node = root.querySelector("[data-status]");
        if (node) {
            node.textContent = value;
            node.classList.toggle("is-error", error);
        }
    }
    async function mutate(payload, success = "已保存") {
        if (busy) return false;
        busy = true;
        root.setAttribute("aria-busy", "true");
        try {
            state = await api(payload);
            render();
            message(success);
            watchAnalysis();
            return true;
        } catch (error) {
            message(error.message, true);
            const node = dialog.querySelector("[data-error]");
            if (node) node.textContent = error.message;
            return false;
        } finally {
            busy = false;
            root.removeAttribute("aria-busy");
        }
    }
    window.loadRecipes = async () => {
        if (busy) return;
        try {
            state = await api();
            if (!loaded) {
                if (state.plan) draft = {
                    members: state.plan.members.map(member => member.id),
                    guests: state.plan.guests,
                    count: state.plan.count,
                    quick: state.plan.quick,
                    meal_type: state.plan.meal_type || "dinner",
                    date: state.plan.date
                };
                loaded = true;
            }
            draft.members = draft.members.filter(key => state.members.some(member => member.id === key));
            render();
            watchAnalysis();
        } catch (error) {
            if (!loaded) root.innerHTML = '<div class="recipe-empty">菜谱暂时无法加载。<button type="button" data-action="reload">重新加载</button></div>';
            message(error.message, true);
        }
    };
    function render() {
        root.innerHTML = `<div class="recipe-heading"><div><span class="recipe-eyebrow">一家人的餐桌</span><h2>菜谱<span>，好好吃饭。</span></h2><p>记下想吃的，把一餐安排妥当。</p></div><div class="recipe-heading-actions"><button type="button" data-action="share">家庭共享 · ${state.account_count} 个账号</button><button type="button" class="primary" data-action="new-recipe">＋ 新增菜谱</button></div></div><nav class="recipe-tabs" aria-label="菜谱栏目">${[ [ "tonight", "今晚吃什么" ], [ "library", "全部菜谱" ], [ "family", "家人口味" ], [ "history", "用餐记录" ] ].map(([key, label]) => `<button type="button" data-recipe-tab="${key}" aria-current="${tab === key ? "page" : "false"}">${label}${key === "library" ? `<span>${state.recipes.length}</span>` : ""}</button>`).join("")}</nav><p data-status role="status" aria-live="polite"></p><div class="recipe-content">${tab === "tonight" ? tonight() : tab === "library" ? library() : tab === "family" ? family() : history()}</div>`;
    }
    function wishList() {
        const pending = state.wishes.filter(wish => !wish.done);
        return `<div class="recipe-section-title"><h3>家人想吃</h3><button type="button" data-action="wish">＋ 点一道</button></div>${pending.length ? `<ul class="recipe-wishes">${pending.map(wish => `<li><div><strong>${esc(wish.name)}</strong><small>${esc(wish.member)} · ${esc(wish.at)}</small></div><button type="button" data-wish-done="${esc(wish.id)}" aria-label="标记已安排 ${esc(wish.name)}">已安排</button>${wish.mine || state.owner ? `<button type="button" class="recipe-icon-button" data-wish-delete="${esc(wish.id)}" aria-label="删除点菜 ${esc(wish.name)}">×</button>` : ""}</li>`).join("")}</ul>` : '<p class="recipe-muted">还没有人点菜。先记下今天想吃什么。</p>'}`;
    }
    function tonight() {
        const plan = state.plan;
        return `<div class="recipe-layout"><div class="recipe-main"><form id="recipe-planner" class="recipe-planner"><div class="recipe-section-title"><h3>今晚，几个人吃？</h3><input aria-label="用餐日期" type="date" name="date" required value="${esc(draft.date)}"></div>${state.members.length ? `<div class="recipe-diners">${state.members.map(member => `<label><input type="checkbox" name="members" value="${esc(member.id)}" ${draft.members.includes(member.id) ? "checked" : ""}><span><strong>${esc(member.name)}</strong><small>${member.portion === 1 ? "常规食量" : quantity(member.portion) + " 份食量"}${member.avoid.length ? " · 有忌口" : ""}</small></span></label>`).join("")}</div>` : '<p class="recipe-muted">先按人数安排，也可以去“家人口味”添加家人和忌口。</p>'}<div class="recipe-plan-controls"><label>餐次<select name="meal_type">${Object.entries(meals).map(([key, label]) => `<option value="${key}" ${draft.meal_type === key ? "selected" : ""}>${label}</option>`).join("")}</select></label><label>${state.members.length ? "临时用餐人数" : "用餐人数"}<input name="guests" type="number" min="0" max="30" step="1" value="${draft.guests}" required></label><label>安排几道<select name="count">${[ 2, 3, 4, 5, 6, 7, 8 ].map(count => `<option value="${count}" ${draft.count === count ? "selected" : ""}>${count} 道</option>`).join("")}</select></label><label class="recipe-quick"><input type="checkbox" name="quick" ${draft.quick ? "checked" : ""}>每道 20 分钟内</label><button class="primary" type="submit">${plan ? "换一组搭配" : "帮我搭配一餐"}</button></div><p class="recipe-hint">已选家人的忌口会自动避开；临时用餐者的忌口请先建档。</p></form>${plan ? planView(plan) : `<div class="recipe-welcome"><div class="recipe-plate" aria-hidden="true"><span>今晚<br><strong>吃什么</strong></span></div><div><h3>不用每天从零想菜单。</h3><p>选好人数，搭配菜品，再带着清单去买菜。</p><button type="button" data-recipe-tab="library">去菜谱里挑一挑 →</button></div></div>`}</div><aside class="recipe-side"><section class="recipe-card">${wishList()}</section><section class="recipe-side-note"><h3>慢慢攒起家的味道</h3><p>已有 <strong>${state.recipes.length}</strong> 道菜。喜欢的家常菜，随时添加进来。</p><button type="button" data-action="new-recipe">写下第一道拿手菜 →</button></section></aside></div><section class="recipe-diet-review" data-nutrition-review>${reviewPanel()}</section>`;
    }
    function planView(plan) {
        const people = plan.members.length + plan.guests;
        return `<section class="recipe-menu"><div class="recipe-section-title"><div><span class="recipe-eyebrow">${esc(plan.date)} · ${meals[plan.meal_type] || "未标餐次"} · ${people} 人 · ${quantity(plan.equivalent)} 份食量</span><h3>这一餐，${plan.recipes.length} 道刚刚好</h3></div><button type="button" data-action="save-plan" ${plan.saved ? "disabled" : ""}>${plan.saved ? "已存用餐记录" : "存入用餐记录"}</button></div>${nutrientView(plan.nutrition)}${plan.notice ? `<p class="recipe-warning">${esc(plan.notice)}</p>` : ""}<ol class="recipe-dish-list">${plan.recipes.map((recipe, index) => `<li><span class="recipe-number">${String(index + 1).padStart(2, "0")}</span><button type="button" data-detail="${esc(recipe.id)}" data-plan-detail="true"><strong>${esc(recipe.name)}</strong><small>${esc(recipe.category)} · ${recipe.minutes} 分钟 · ${quantity(plan.equivalent / recipe.servings)} 倍配方</small></button><button type="button" class="recipe-icon-button" data-remove-dish="${esc(recipe.id)}" aria-label="移出餐单 ${esc(recipe.name)}">×</button></li>`).join("")}</ol><button type="button" class="recipe-add-dish" data-recipe-tab="library">＋ 从菜谱加一道</button><details class="recipe-order"><summary>备餐顺序参考</summary><ol>${[ ...plan.recipes ].sort((first, second) => second.minutes - first.minutes).map(recipe => `<li>先准备${esc(recipe.name)}<span>约 ${recipe.minutes} 分钟</span></li>`).join("")}</ol><p class="recipe-hint">先开火做耗时长的菜；可并行备料，时间按厨具和熟练度调整。</p></details></section>${shopping(plan)}`;
    }
    function nutrientView(nutrition, compact = false) {
        if (!nutrition) return "";
        const targets = state.nutrition_targets || {};
        return `<section class="recipe-nutrition" aria-label="本餐营养估算"><div class="recipe-nutrition-heading"><span>本餐每成人份 · 估算</span><button type="button" data-action="nutrition-targets">每日参考目标 ↗</button></div><div class="recipe-nutrients">${nutrients.map(([key, label]) => {
            const value = nutrition.per_serving[key], target = targets[key];
            const percent = value !== null && target ? Math.min(100, value / target * 100) : 0;
            return `<div class="recipe-nutrient" data-nutrient="${key}"><strong>${label}</strong><div class="recipe-nutrient-track" role="meter" aria-label="${label}：本餐每成人份${value === null ? "未知" : "约" + value + "克"}${target ? "，每日参考" + target + "克" : "，未设每日目标"}" aria-valuemin="0" aria-valuemax="${target || 100}" aria-valuenow="${value === null ? 0 : Math.min(value, target || 100)}"><span style="width:${percent}%"></span></div><p>${value === null ? "—" : "≈ " + quantity(value)}${target ? ` / ${quantity(target)} 克` : " 克"}</p>${!target ? '<small>未设每日目标</small>' : ""}</div>`;
        }).join("")}</div><p class="recipe-hint">${nutrition.partial ? "部分估算，缺项未当作 0。" : "按配方原料估算。"}本餐与全天目标对照；不是个人实际摄入，也不代表已达到全天目标。</p>${!compact ? `<details class="recipe-nutrition-basis"><summary>查看估算依据${nutrition.partial ? ` · ${nutrition.missing.length} 项待补充` : ""}</summary>${nutrition.missing.length ? `<p>未计入：${nutrition.missing.map(esc).join("；")}。</p>` : ""}${nutrition.approximate.length ? `<p>计数或可食部换算：${nutrition.approximate.map(esc).join("、")}。</p>` : ""}<p>按生重、可食部和成人食量折算；烹调损耗、实际分餐会有差异。可在编辑菜谱时补齐用量，或填写可靠来源的每份营养值。</p><a href="${esc(nutrition.source_url)}" target="_blank" rel="noopener">参考：USDA SR28 食物成分数据</a></details>` : ""}</section>`;
    }
    function reviewPanel() {
        const review = state.nutrition_review, advice = state.nutrition_advice;
        const pending = advice?.status === "pending";
        return `<div class="recipe-section-title"><div><span class="recipe-eyebrow">最近 7 天 · ${review.day_count} 天 / ${review.meal_count} 餐已确认</span><h3>和山君一起，好好吃饭</h3></div></div><p class="recipe-muted">只分析确认吃过的餐次；只记晚餐时，结论只覆盖晚餐。已关联健康的用餐成员会获得针对性建议。全家按成人份折算，不能代替个人全天饮食记录。</p><div class="recipe-diet-facts">${review.observations.map(text => `<p>${esc(text)}</p>`).join("")}</div>${review.daily.length ? `<details class="recipe-nutrition-basis"><summary>查看每天已记录的营养</summary><div class="recipe-daily-list">${review.daily.map(day => `<p><strong>${esc(day.date)} · ${day.slots.map(slot => meals[slot] || "未标餐次").join("、")} · ${day.meals} 餐</strong><br>${nutrients.map(([key, label]) => `${label} ${day.per_serving[key] === null ? "未知" : "约" + quantity(day.per_serving[key]) + "克"}`).join(" · ")}${day.partial ? "（部分估算）" : ""}</p>`).join("")}</div></details>` : ""}<div class="recipe-advice-actions"><button type="button" class="primary" data-action="analyze" ${pending || !analysisAllowed() ? "disabled" : ""}>${pending ? "山君正在分析…" : "请山君分析"}</button><button type="button" data-action="analysis-refresh">刷新分析</button></div>${!analysisAllowed() ? '<p class="recipe-hint">正式账号可请山君分析，游客可体验营养估算。</p>' : '<p class="recipe-hint">确认吃过后会请山君分析；也可主动分析当前餐单。相同记录复用结果，仅发送餐单、忌口和营养摘要。</p>'}${advice?.status === "completed" ? `<div class="recipe-shanjun"><span class="recipe-eyebrow">山君原话 · ${new Date(advice.completed_at * 1000).toLocaleString("zh-CN")}</span><div>${esc(advice.result)}</div></div>` : advice && !pending ? `<p class="recipe-warning">${esc(advice.error || "暂未取得分析结果，未自动重发。")}</p>` : ""}${advice ? '<p class="recipe-hint">建议基于提交时的记录；补充餐单后可再次分析。</p>' : ""}<p class="recipe-hint">一般饮食建议参考 <a href="https://dg.cnsoc.org/article/04/70JvPbFmTlyZbjoO67LeRg.html" target="_blank" rel="noopener">中国居民膳食指南</a>。每日参考目标用于成人；儿童及特殊营养需求请单独评估。</p>`;
    }
    function watchAnalysis() {
        clearTimeout(analysisTimer);
        if (state?.nutrition_advice?.status !== "pending") { analysisPolls = 0; return; }
        if (analysisPolls >= 10 || root.hidden) return;
        analysisTimer = setTimeout(async () => {
            if (root.hidden || busy) return;
            analysisPolls++;
            try {
                state = await api();
                const panel = root.querySelector("[data-nutrition-review]");
                if (panel) panel.innerHTML = reviewPanel();
                watchAnalysis();
            } catch (_) { message("分析状态暂时无法更新，可手动刷新。", true); }
        }, 12000);
    }
    function targetsEditor() {
        open("成人每日参考目标", `<p class="recipe-muted">仅用来与本餐估算对照。按你已有的饮食目标填写；没有目标可留空。这里不会根据家庭人数推断每个人的需求。</p><div class="recipe-target-fields">${nutrients.map(([key, label]) => input(label + "（克 / 天）", key, state.nutrition_targets?.[key] ?? "", "number", 'min="0.1" max="2000" step="0.1"')).join("")}</div><p class="recipe-hint">膳食纤维包含全谷物、杂豆、蔬果中的纤维；不采用“粗纤维”的检测口径。</p>`, "nutrition-targets");
    }
    function healthSummary(profile) {
        const measured = (key, unit) => profile?.[key] ? `${quantity(profile[key].value)} ${unit} · ${esc(profile[key].date)}` : "未记录";
        const stage = {adult: "成人", child: "儿童 / 青少年", pregnant: "孕期 / 哺乳期", older: "老年人"};
        return `<div class="recipe-health-summary"><p>身高：${measured("height_cm", "cm")}<br>体重：${measured("weight_kg", "kg")}</p><p>生命阶段：${stage[profile?.life_stage] || "未记录"}<br>已记录健康情况：${profile?.conditions?.length ? profile.conditions.map(esc).join("、") : "未记录"}</p><p>饮食习惯：${esc(profile?.habits || "未记录")}</p></div>`;
    }
    function healthEditor() {
        const profile = state.my_nutrition_health || {};
        open("我的饮食与健康资料", `${healthSummary(profile)}<p class="recipe-hint">身高、体重读取健康模块最近的有效测量，并保留各自日期。可在健康页补充体测、就诊或体检报告。</p><label>生命阶段<select name="life_stage">${Object.entries({adult: "成人", child: "儿童 / 青少年", pregnant: "孕期 / 哺乳期", older: "老年人"}).map(([key, label]) => `<option value="${key}" ${profile.life_stage === key ? "selected" : ""}>${label}</option>`).join("")}</select></label>${textarea("已确诊或本人记录的健康情况 · 逗号分隔", "conditions", (profile.conditions || []).join("，"), 'rows="3" maxlength="1200" placeholder="例如：脂肪肝，高血压。未确认的情况请勿填写为诊断"')}${textarea("日常饮食习惯与医嘱中的饮食要求", "habits", profile.habits || "", 'rows="4" maxlength="600" placeholder="例如：常喝含糖饮料，晚餐主食多；医生要求限制某类食物"')}<p class="recipe-hint">保存到当前账号的健康库。只有在“家人口味”主动关联后，摘要才供同一厨房和山君饮食分析使用；医疗原件不随关联共享。</p>`, "health-profile");
    }
    function healthLink(id) {
        const member = state.members.find(item => item.id === id);
        if (!member) return;
        const profile = member.health || state.my_nutrition_health;
        open(`${member.name} · 饮食健康关联`, `${healthSummary(profile)}<p class="recipe-hint">${member.health ? "已关联成员授权的健康摘要，身高体重会随其健康记录更新。" : "下面的关联按钮会将当前登录账号的健康摘要关联到这位家人，请只关联本人的成员记录。"}同一厨房可查看这些摘要；山君仅在饮食分析时读取已关联用餐成员的摘要。</p>${!member.health ? `<button class="primary" type="button" data-link-health="${esc(id)}">关联我的健康摘要到这位成员</button>` : member.health_mine || state.owner ? `<button type="button" data-unlink-health="${esc(id)}">解除关联并清除本厨房的分析结果</button>` : ""}${!member.health || member.health_mine ? '<button type="button" data-action="health-editor">编辑我的饮食与健康资料</button>' : ""}`);
    }
    function shopping(plan) {
        const items = plan.shopping;
        const needed = items.filter(item => item.purchase > 0 || item.unmeasured);
        const done = needed.filter(item => item.checked).length;
        return `<section class="recipe-shopping"><div class="recipe-section-title"><div><span class="recipe-eyebrow">按 ${quantity(plan.equivalent)} 份食量合并</span><h3>带着清单去买菜</h3></div><div><button type="button" data-action="pantry">家里已有</button><button type="button" data-action="copy">复制清单</button></div></div><div class="recipe-progress"><span style="width:${needed.length ? 100 * done / needed.length : 0}%"></span></div><p class="recipe-hint">${done} / ${needed.length} 项已备齐。同名同单位合并、扣除库存；克向上取整到 10g，计数食材向上取整。</p><ul class="recipe-shopping-list">${items.map(item => `<li class="${item.checked ? "is-checked" : ""}"><label><input type="checkbox" data-shopping="${esc(item.id)}" ${item.checked ? "checked" : ""}><span>${esc(item.name)}${item.seasoning ? "<small>调料</small>" : ""}</span></label><div><strong>${item.purchase > 0 ? amount(item, "purchase") : item.unmeasured ? "适量自备" : "库存够用"}</strong><small>${item.stock ? "已有 " + amount(item, "stock") + " · " : ""}${item.quantity ? "配方需 " + amount(item) : ""}${item.unmeasured ? item.quantity ? "，另有适量用量" : "按口味准备" : ""}</small></div></li>`).join("")}</ul><p class="recipe-hint">食材按采购前生鲜用量填写，调料按口味增减；米、面为下锅前干重。采购勾选不会自动扣减库存。</p></section>`;
    }
    function visibleRecipes() {
        return state.recipes.filter(recipe => (category === "全部" || recipe.category === category) && (recipe.name + " " + recipe.ingredients.map(item => item.name).join(" ")).includes(query));
    }
    function library() {
        return `<div class="recipe-library-tools"><label class="recipe-search"><span>搜索菜名或食材</span><input type="search" id="recipe-search" placeholder="例如：番茄、鸡蛋" value="${esc(query)}"></label><div class="recipe-filters" role="group" aria-label="菜谱分类">${[ "全部", "荤菜", "素菜", "汤羹", "主食", "快手" ].map(item => `<button type="button" aria-pressed="${category === item}" data-category="${item}">${item}</button>`).join("")}</div></div><p class="recipe-hint">你的菜谱会保存在家庭厨房中，加入同一厨房的家人也能查看和补充。</p><div id="recipe-library-grid" class="recipe-library-grid">${cards()}</div>`;
    }
    function cards() {
        return visibleRecipes().map(recipe => `<article class="recipe-library-item"><span class="recipe-food-mark" aria-hidden="true">${recipe.category === "主食" ? "饭" : recipe.category === "汤羹" ? "汤" : recipe.tags.includes("蔬菜") ? "蔬" : "食"}</span><div class="recipe-card-body"><span class="recipe-eyebrow">${esc(recipe.category)} · ${recipe.minutes} 分钟</span><h3><button type="button" data-detail="${esc(recipe.id)}">${esc(recipe.name)}</button></h3><p>${recipe.ingredients.slice(0, 3).map(item => esc(item.name)).join(" / ")}</p><div class="recipe-card-actions"><button type="button" data-add-dish="${esc(recipe.id)}">＋ 加入餐单</button><button type="button" data-edit-recipe="${esc(recipe.id)}" aria-label="编辑 ${esc(recipe.name)}">编辑</button></div></div></article>`).join("") || '<p class="recipe-empty">还没有符合条件的菜谱。换个关键词，或新增一道。</p>';
    }
    function family() {
        return `<div class="recipe-layout"><div><div class="recipe-section-title"><div><h3>每个人的口味，都记得</h3><p class="recipe-muted">选择谁来吃饭，就按谁的忌口筛选。</p></div><button type="button" class="primary" data-action="member">＋ 添加家人</button></div><div class="recipe-member-list">${state.members.map(member => `<article><span class="recipe-avatar">${esc(member.name.slice(0, 1))}</span><div><h4>${esc(member.name)}</h4><p>${quantity(member.portion)} 份食量</p><div class="recipe-avoid-tags">${member.avoid.length ? member.avoid.map(term => `<span>${esc(term)}</span>`).join("") : '<span class="recipe-muted">尚未记录忌口</span>'}</div></div><div class="recipe-member-actions"><button type="button" data-edit-member="${esc(member.id)}">编辑</button><button type="button" data-health-member="${esc(member.id)}">${member.health ? "已关联健康 · 查看" : "关联健康资料"}</button></div></article>`).join("") || '<div class="recipe-empty">添加家人，记录忌口与食量。儿童或长辈也可以由你代为记录。</div>'}</div><p class="recipe-hint">忌口按食材名称与常见别名匹配，不覆盖所有复合配料或交叉接触；过敏成员仍需核对包装和实际做法。</p></div><aside class="recipe-side"><section class="recipe-card">${wishList()}</section></aside></div>`;
    }
    function history() {
        return `<div class="recipe-section-title"><div><h3>家的餐桌日记</h3><p class="recipe-muted">保存餐单留作计划；确认吃过后，才计入近期饮食分析。每份为成人食量折算。</p></div></div><section class="recipe-diet-review" data-nutrition-review>${reviewPanel()}</section>${state.history.map(meal => `<article class="recipe-history"><div><span class="recipe-eyebrow">${esc(meal.date)} · ${meals[meal.meal_type] || "未标餐次"} · ${meal.members.length + meal.guests} 人 · ${meal.consumed ? "已确认吃过" : "计划餐单"}</span><h3>${meal.recipes.map(recipe => esc(recipe.name)).join("、")}</h3><p>${esc(meal.feedback || "这一餐，口味和份量合适吗？")}</p></div><div class="recipe-history-actions"><button type="button" data-consume="${esc(meal.id)}" ${!meal.consumed && meal.date > localDay() ? "disabled" : ""}>${meal.consumed ? "撤销吃过确认" : analysisAllowed() ? "确认吃过并分析" : "确认吃过"}</button><button type="button" data-feedback="${esc(meal.id)}">记下反馈</button></div><details><summary>查看当时菜谱、营养与清单</summary>${nutrientView(meal.nutrition, true)}${meal.recipes.map(recipe => `<button type="button" data-detail="${esc(recipe.id)}" data-meal="${esc(meal.id)}">${esc(recipe.name)}</button>`).join("")}<p>${meal.shopping.map(item => esc(item.name) + " " + (item.purchase ? amount(item, "purchase") : item.unmeasured ? "适量" : "库存够用")).join(" · ")}</p></details></article>`).join("") || '<div class="recipe-empty">安排好一餐后，点“存入用餐记录”，就会留在这里。</div>'}`;
    }
    function open(title, body, formType = "") {
        dialog.innerHTML = `<div class="recipe-dialog-heading"><h2>${esc(title)}</h2><button type="button" data-action="close" aria-label="关闭">×</button></div>${formType ? `<form data-form="${formType}">${body}<p data-error role="alert"></p><div class="recipe-dialog-footer"><button type="button" data-action="close">取消</button><button class="primary" type="submit">保存</button></div></form>` : body}`;
        if (!dialog.open) dialog.showModal();
    }
    function input(label, name, value = "", type = "text", extra = "") {
        return `<label>${label}<input name="${name}" type="${type}" value="${esc(value)}" ${extra}></label>`;
    }
    function textarea(label, name, value = "", extra = "") {
        return `<label>${label}<textarea name="${name}" ${extra}>${esc(value)}</textarea></label>`;
    }
    function recipeEditor(id) {
        const recipe = state.recipes.find(item => item.id === id) || {
            name: "",
            category: "荤菜",
            minutes: 20,
            servings: 2,
            ingredients: [],
            seasonings: [],
            steps: [],
            tags: []
        };
        const lines = items => items.map(item => `${item.name} | ${item.quantity === null ? "适量" : item.quantity} | ${item.unit}`).join("\n");
        open(id ? "编辑菜谱" : "新增菜谱", `<input type="hidden" name="id" value="${esc(id || "")}">${input("菜名", "name", recipe.name, "text", 'required maxlength="60"')}<div class="recipe-form-row"><label>分类<select name="category">${[ "荤菜", "素菜", "汤羹", "主食", "快手" ].map(item => `<option ${recipe.category === item ? "selected" : ""}>${item}</option>`).join("")}</select></label>${input("耗时（分钟）", "minutes", recipe.minutes, "number", 'required min="1" max="600" step="1"')}${input("基础配方（几份食量）", "servings", recipe.servings, "number", 'required min="0.25" max="30" step="0.25"')}</div>${textarea("食材 · 每行：名称 | 数量 | 单位", "ingredients", lines(recipe.ingredients), 'required rows="5" placeholder="番茄 | 300 | g&#10;鸡蛋 | 3 | 个"')}<p class="recipe-hint">填写生鲜采购用量；单位支持 ${units.join("、")}。无法计量可填“适量”。</p>${textarea("调料 · 同样每行一项", "seasonings", lines(recipe.seasonings), 'rows="3" placeholder="生抽 | 1 | 勺"')}${input("搭配与食材标签 · 逗号分隔", "tags", recipe.tags.join("，"), "text", 'placeholder="蔬菜，蛋白，主食，花生" maxlength="300"')}${textarea("制作步骤 · 每行一步", "steps", recipe.steps.join("\n"), 'required rows="5"')}<details class="recipe-nutrition-basis"><summary>补充每成人份营养值（可选）</summary><p class="recipe-hint">有包装标签或可靠配方数据时填写，四项全部填写后优先采用；不知道就全部留空，按食材估算。每份口径与上面的基础配方一致。</p><div class="recipe-target-fields">${nutrients.map(([key, label]) => input(label + "（克 / 份）", "nutrition_" + key, recipe.nutrition_per_serving?.[key] ?? "", "number", 'min="0" max="2000" step="0.1"')).join("")}</div></details>${id ? `<button type="button" class="recipe-danger" data-delete-recipe="${esc(id)}">删除这道菜谱</button>` : ""}`, "recipe");
    }
    function memberEditor(id) {
        const member = state.members.find(item => item.id === id) || {
            name: "",
            portion: 1,
            avoid: []
        };
        open(id ? "编辑家人口味" : "添加家人", `<input type="hidden" name="id" value="${esc(id || "")}">${input("怎么称呼", "name", member.name, "text", 'required maxlength="30"')}${input("食量 · 成人常规为 1，可按实际调整", "portion", member.portion, "number", 'required min="0.25" max="2" step="0.05"')}${textarea("忌口或过敏食材 · 逗号分隔", "avoid", member.avoid.join("，"), 'rows="3" placeholder="虾，花生，香菜，不吃辣请填“辣”"')}<p class="recipe-hint">常见分组支持海鲜、鱼、虾、蛋、奶、花生、大豆、小麦、坚果、辣、猪肉。也可以填写具体食材名。</p>${id ? `<button type="button" class="recipe-danger" data-delete-member="${esc(id)}">移除这位成员</button>` : ""}`, "member");
    }
    function detail(id, meal, planDetail) {
        const recipe = (meal ? state.history.find(item => item.id === meal)?.recipes : planDetail ? state.plan?.recipes : state.recipes)?.find(item => item.id === id);
        if (!recipe) return;
        const scale = meal ? state.history.find(item => item.id === meal).equivalent / recipe.servings : planDetail ? state.plan.equivalent / recipe.servings : 1;
        const items = field => recipe[field].map(item => `<li><span>${esc(item.name)}</span><strong>${item.quantity === null ? esc(item.note || "适量") : quantity(item.quantity * scale) + esc(item.unit)}</strong></li>`).join("");
        open(recipe.name, `<p class="recipe-muted">${recipe.minutes} 分钟 · ${quantity(recipe.servings * scale)} 份食量${scale !== 1 ? " · 已按本餐缩放" : ""}</p><h3>食材</h3><ul class="recipe-detail-ingredients">${items("ingredients")}</ul>${recipe.seasonings.length ? `<h3>调料</h3><ul class="recipe-detail-ingredients">${items("seasonings")}</ul>` : ""}<h3>做法</h3><ol class="recipe-steps">${recipe.steps.map(step => `<li>${esc(step)}</li>`).join("")}</ol>${!meal && !planDetail ? `<button type="button" data-edit-recipe="${esc(id)}">编辑这道菜</button>` : ""}`);
    }
    function readPlanner() {
        const form = root.querySelector("#recipe-planner");
        if (form) {
            const fields = new FormData(form);
            draft = {
                members: fields.getAll("members"),
                guests: Number(fields.get("guests")),
                count: Number(fields.get("count")),
                quick: fields.has("quick"),
                meal_type: fields.get("meal_type"),
                date: fields.get("date")
            };
        }
        return {
            ...draft
        };
    }
    function parseItems(value) {
        return String(value).split("\n").filter(line => line.trim()).map(line => {
            const parts = line.split("|").map(part => part.trim());
            if (parts.length !== 3 || !parts[0] || !units.includes(parts[2]) || ![ "适量", "少许" ].includes(parts[1]) && (!parts[1] || !Number.isFinite(Number(parts[1])))) throw Error("食材请按“名称 | 数量 | 单位”填写，每行一项");
            return {
                name: parts[0],
                quantity: [ "适量", "少许" ].includes(parts[1]) ? null : Number(parts[1]),
                unit: parts[2],
                note: ""
            };
        });
    }
    const split = value => String(value).split(/[,，、\n]/).map(item => item.trim()).filter(Boolean);
    async function submit(event) {
        event.preventDefault();
        const form = event.target;
        if (form.id === "recipe-planner") {
            if (await mutate({
                action: "plan-generate",
                ...readPlanner()
            }, "已按人数和忌口搭配")) return;
            return;
        }
        const fields = new FormData(form), type = form.dataset.form;
        let payload;
        try {
            if (type === "recipe") payload = {
                action: "recipe-save",
                recipe: {
                    id: fields.get("id"),
                    name: fields.get("name"),
                    category: fields.get("category"),
                    minutes: Number(fields.get("minutes")),
                    servings: Number(fields.get("servings")),
                    tags: split(fields.get("tags")),
                    ingredients: parseItems(fields.get("ingredients")),
                    seasonings: parseItems(fields.get("seasonings")),
                    steps: String(fields.get("steps")).split("\n").map(step => step.trim()).filter(Boolean)
                }
            };
            if (type === "recipe") {
                const values = nutrients.map(([key]) => String(fields.get("nutrition_" + key) || "").trim());
                if (values.some(Boolean)) {
                    if (!values.every(Boolean)) throw Error("营养值请完整填写四项，或全部留空");
                    payload.recipe.nutrition_per_serving = Object.fromEntries(nutrients.map(([key], index) => [key, Number(values[index])]));
                }
            }
            if (type === "nutrition-targets") payload = {
                action: "nutrition-targets",
                targets: Object.fromEntries(nutrients.map(([key]) => [key, fields.get(key) === "" ? null : Number(fields.get(key))]))
            };
            if (type === "health-profile") payload = {
                action: "health-profile-save", conditions: split(fields.get("conditions")),
                habits: fields.get("habits"), life_stage: fields.get("life_stage")
            };
            if (type === "member") payload = {
                action: "member-save",
                id: fields.get("id"),
                name: fields.get("name"),
                portion: Number(fields.get("portion")),
                avoid: split(fields.get("avoid"))
            };
            if (type === "wish") payload = {
                action: "wish-add",
                member: fields.get("member"),
                name: fields.get("name")
            };
            if (type === "pantry") payload = {
                action: "pantry-save",
                items: parseItems(fields.get("items")).map(item => {
                    if (item.quantity === null) throw Error("库存需要填写实际数量");
                    return item;
                })
            };
            if (type === "feedback") payload = {
                action: "history-feedback",
                id: fields.get("id"),
                feedback: fields.get("feedback")
            };
            if (type === "join") payload = {
                action: "join",
                code: fields.get("code")
            };
            if (payload) {
                form.querySelector('[type="submit"]').disabled = true;
                if (await mutate(payload, type === "join" ? "已加入家庭厨房" : "已保存")) {
                    if (type === "join") {
                        draft = {
                            members: [],
                            guests: 2,
                            count: 4,
                            quick: false,
                            meal_type: "dinner",
                            date: localDay()
                        };
                        render();
                    }
                    dialog.close();
                }
            }
        } catch (error) {
            form.querySelector("[data-error]").textContent = error.message;
        } finally {
            const button = form.querySelector('[type="submit"]');
            if (button) button.disabled = false;
        }
    }
    async function changeDish(id, remove) {
        readPlanner();
        const current = state.plan?.recipes.map(recipe => recipe.id) || [];
        const selected = remove ? current.filter(key => key !== id) : [ ...new Set([ ...current, id ]) ];
        if (!selected.length) {
            message("餐单至少保留一道菜，或重新搭配一组。", true);
            return;
        }
        if (await mutate({
            action: "plan-generate",
            ...draft,
            recipes: selected
        }, "餐单已调整")) {
            tab = "tonight";
            render();
        }
    }
    function pantry() {
        open("家里已有的食材与调料", `${textarea("每行：名称 | 数量 | 单位", "items", state.pantry.map(item => `${item.name} | ${item.quantity} | ${item.unit}`).join("\n"), 'rows="9" placeholder="大米 | 500 | g&#10;生抽 | 10 | 勺"')}<p class="recipe-hint">同名同单位才会扣除。做完饭后可在这里更新余量。</p>`, "pantry");
    }
    async function click(event) {
        const button = event.target.closest("button");
        if (!button || busy) return;
        const data = button.dataset;
        if (data.action === "health-editor") { healthEditor(); return; }
        if (data.healthMember) { healthLink(data.healthMember); return; }
        if (data.linkHealth || data.unlinkHealth) {
            if (await mutate({action: data.linkHealth ? "health-link" : "health-unlink", id: data.linkHealth || data.unlinkHealth}, data.linkHealth ? "已关联本人健康摘要" : "已解除健康关联并清除分析结果")) dialog.close();
            return;
        }
        if (data.action === "nutrition-targets") { targetsEditor(); return; }
        if (data.action === "analysis-refresh") { await window.loadRecipes(); return; }
        if (data.action === "analyze") {
            analysisPolls = 0;
            await mutate({action: "analysis-request"}, "已提交山君分析；相同记录会复用结果");
            return;
        }
        if (data.consume) {
            const meal = state.history.find(item => item.id === data.consume);
            if (!meal) return;
            const consumed = !meal.consumed;
            if (await mutate({action: "history-consumed", id: meal.id, consumed}, consumed ? "已确认吃过，计入近期饮食记录" : "已撤销确认，不再计入近期分析")) {
                if (consumed && analysisAllowed()) {
                    analysisPolls = 0;
                    await mutate({action: "analysis-request"}, "已确认吃过，并提交山君分析");
                }
            }
            return;
        }
        if (data.recipeTab) {
            readPlanner();
            tab = data.recipeTab;
            render();
            return;
        }
        if (data.category) {
            category = data.category;
            render();
            return;
        }
        if (data.detail) {
            detail(data.detail, data.meal, data.planDetail);
            return;
        }
        if (data.editRecipe) {
            recipeEditor(data.editRecipe);
            return;
        }
        if (data.editMember) {
            memberEditor(data.editMember);
            return;
        }
        if (data.addDish) {
            await changeDish(data.addDish, false);
            return;
        }
        if (data.removeDish) {
            await changeDish(data.removeDish, true);
            return;
        }
        if (data.wishDone) {
            await mutate({
                action: "wish-done",
                id: data.wishDone
            }, "点菜状态已更新");
            return;
        }
        if (data.wishDelete) {
            await mutate({
                action: "wish-delete",
                id: data.wishDelete
            }, "已删除点菜记录");
            return;
        }
        if (data.deleteRecipe || data.deleteMember) {
            const recipe = !!data.deleteRecipe;
            if (await tiangongConfirm({
                title: recipe ? "删除这道菜谱？" : "移除这位家人？",
                message: recipe ? "已保存的用餐记录会保留当时的配方。" : "历史餐单保留；涉及这位家人的当前餐单需要重新搭配。",
                confirmLabel: "删除",
                cancelLabel: "取消",
                danger: true
            })) {
                if (await mutate({
                    action: recipe ? "recipe-delete" : "member-delete",
                    id: data.deleteRecipe || data.deleteMember
                }, "已删除")) dialog.close();
            }
            return;
        }
        if (data.feedback) {
            const meal = state.history.find(item => item.id === data.feedback);
            open("记下这一餐", `<input type="hidden" name="id" value="${esc(meal.id)}">${textarea("口味、剩菜、下次调整", "feedback", meal.feedback, 'rows="5" maxlength="300"')}`, "feedback");
            return;
        }
        if (data.action === "close") {
            dialog.close();
            return;
        }
        if (data.action === "reload") {
            await window.loadRecipes();
            return;
        }
        if (data.action === "new-recipe") {
            recipeEditor();
            return;
        }
        if (data.action === "member") {
            memberEditor();
            return;
        }
        if (data.action === "wish") {
            open("今天想吃什么", `<label>谁想吃<select name="member"><option>我</option>${state.members.map(member => `<option>${esc(member.name)}</option>`).join("")}</select></label>${input("想吃的菜", "name", "", "text", 'required maxlength="60" placeholder="例如：番茄炒蛋"')}`, "wish");
            return;
        }
        if (data.action === "pantry") {
            pantry();
            return;
        }
        if (data.action === "save-plan") {
            await mutate({
                action: "plan-save"
            }, "已保存这餐快照");
            return;
        }
        if (data.action === "copy") {
            try {
                const content = [ `${state.plan.date} 采购清单`, ...state.plan.shopping.filter(item => item.purchase || item.unmeasured).map(item => `${item.checked ? "☑" : "□"} ${item.name} ${item.purchase ? amount(item, "purchase") : "适量"}${item.unmeasured && item.purchase ? "，另按口味增减" : ""}`) ].join("\n");
                await navigator.clipboard.writeText(content);
                message("采购清单已复制");
            } catch {
                message("复制未成功，可直接查看页面上的采购清单。", true);
            }
            return;
        }
        if (data.action === "share") {
            open("一家人，共用一本菜谱", `<p>加入后共享菜谱、家人口味、点菜、餐单与采购清单。各账号的财务、健康和笔记仍各自保管。</p><p class="recipe-hint">家人先登录自己的正式账号，再输入厨房邀请码。加入会切换当前厨房，旧厨房资料保留，不会自动合并。</p>${state.owner ? '<button type="button" data-action="invite">生成一次性邀请码</button><p id="recipe-invite-code"></p>' : ""}<form data-form="join">${input("加入家人的厨房", "code", "", "text", 'required maxlength="100" autocomplete="off" placeholder="粘贴邀请码"')}<p data-error role="alert"></p><button class="primary" type="submit">加入家庭厨房</button></form>`);
            return;
        }
        if (data.action === "invite") {
            try {
                const result = await api({
                    action: "invite"
                });
                const node = dialog.querySelector("#recipe-invite-code");
                node.textContent = `${result.invite}（24 小时有效，仅可使用一次；再次生成会使旧码失效）`;
            } catch (error) {
                message(error.message, true);
                open("暂时无法邀请", `<p>${esc(error.message)}</p>`);
            }
            return;
        }
    }
    root.addEventListener("click", click);
    dialog.addEventListener("click", click);
    root.addEventListener("submit", submit);
    dialog.addEventListener("submit", submit);
    root.addEventListener("change", async event => {
        if (event.target.dataset.shopping) await mutate({
            action: "shopping-check",
            id: event.target.dataset.shopping
        }, "采购状态已保存"); else if (event.target.closest("#recipe-planner")) readPlanner();
    });
    root.addEventListener("input", event => {
        if (event.target.id === "recipe-search") {
            query = event.target.value;
            root.querySelector("#recipe-library-grid").innerHTML = cards();
        }
    });
})();
