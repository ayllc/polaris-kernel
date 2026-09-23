#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apr.c patch v3  —— 不再依赖 ADSP-up 事件链, 直接下发 apr 的 DT 子设备

背景 (MIUI 12.5.1.0.QDGCNXM / polaris / 4.9.186):
  vendor 侧音频模块 (snd-soc-sdm845 machine driver / wcd934x codec /
  swr_wcd_ctrl / wcd-spi ...) 全部 load 成功, 但声卡不注册,
  /dev/snd/ 只有 timer -> 无声, 且视频因音频轨道超时被拖成加速播放。

根因链 (已逐层核实):
  * DTB 里 qcom,msm-audio-apr 节点下只有一个子节点 sound-tavil
    (compatible = qcom,sdm845-asoc-snd-tavil), 它就是 vendor machine
    driver 要绑的 device。msm-pcm-* 之类都在 /soc 下, 由 simple-bus
    在 boot 早期自动 populate, 与 apr 无关。
  * techpack/audio/ipc/apr.c 只在 AUDIO_NOTIFIER_SERVICE_UP 事件到达时
    才 of_platform_populate(), 该事件走:
       audio_notifier(PDR) -> audio_pdr.c -> service-locator
       -> QMI over ipc_router -> ADSP "avs/audio"
    事件链一断, sound-tavil 永不出现, machine driver 无 device 可绑。
  * 但 ADSP 固件本身是另一条独立链路:
       /sys/kernel/boot_adsp/boot 写 1 -> adsp-loader.c -> subsystem_get("adsp")
       -> 成功后 apr_set_q6_state(APR_SUBSYS_LOADED)
    即 q6 状态可以先于 up 事件变成 LOADED。

本补丁:
  1) apr_add_child_devices(): dev_dbg -> dev_info, 事件到达与否日志可见;
  2) 新增 apr_fallback_populate(): 轮询 apr_get_q6_state(),
     一旦 != DOWN (说明 ADSP 固件已由 adsp-loader 装载) 就立刻
     of_platform_populate() 建 sound-tavil, 不再等 PDR/SSR 事件;
     若 2 分钟内始终 DOWN, 也强制建一次 (至少让设备节点与诊断可见)。
     of_platform_populate 幂等, 重复调用无副作用。
  3) apr_adsp_up(): pr_debug -> pr_info, 若事件链哪天通了也能在 dmesg 看到。
"""
import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "apr.c"
s = open(path, encoding="utf-8").read()
orig = s

# 0) 需要的头
if "#include <linux/jiffies.h>" not in s:
    s = s.replace("#include <linux/of_platform.h>",
                  "#include <linux/of_platform.h>\n"
                  "#include <linux/jiffies.h>\n"
                  "#include <linux/workqueue.h>", 1)

# 1) apr_add_child_devices 加日志 + 追加兜底轮询函数
pat = re.compile(r"static void apr_add_child_devices\(struct work_struct \*work\)\s*\{.*?\n\}", re.S)
m = pat.search(s)
assert m, "apr_add_child_devices() not found"
new_fn = '''static void apr_add_child_devices(struct work_struct *work)
{
\tint ret;

\tdev_info(apr_priv->dev, "%s: populating child devices\\n", __func__);
\tret = of_platform_populate(apr_priv->dev->of_node,
\t\t\tNULL, NULL, apr_priv->dev);
\tif (ret)
\t\tdev_err(apr_priv->dev, "%s: failed to add child nodes, ret=%d\\n",
\t\t\t __func__, ret);
}

static struct delayed_work apr_fallback_populate_work;
static int apr_fallback_tries;

/*
 * 兜底: 不依赖 audio_notifier 的 ADSP-up 事件, 只要 ADSP 固件已被
 * adsp-loader 装载 (q6_state != DOWN) 就直接下发 apr 的 DT 子设备
 * (sound-tavil), 让 vendor machine driver 有 device 可绑。
 */
static void apr_fallback_populate(struct work_struct *work)
{
\tint st;
\tbool force = false;

\tif (!apr_priv || !apr_priv->dev)
\t\treturn;

\tst = apr_get_q6_state();
\tif (st == APR_SUBSYS_DOWN) {
\t\t/* ADSP 固件尚未装载完, 2s 后再探; 最多等 2 分钟 */
\t\tif (++apr_fallback_tries < 60) {
\t\t\tschedule_delayed_work(&apr_fallback_populate_work,
\t\t\t\t\t      msecs_to_jiffies(2000));
\t\t\treturn;
\t\t}
\t\tforce = true;
\t}

\tdev_info(apr_priv->dev, "%s: try=%d q6_state=%d force=%d\\n",
\t\t\t__func__, apr_fallback_tries, st, force);
\tof_platform_populate(apr_priv->dev->of_node, NULL, NULL, apr_priv->dev);
\tdev_info(apr_priv->dev, "%s: populate done\\n", __func__);
}'''
s = s[:m.start()] + new_fn + s[m.end():]

# 2) apr_adsp_up 可见化
old_up = 'static void apr_adsp_up(void)\n{\n\tpr_debug("%s: Q6 is Up\\n", __func__);'
assert old_up in s, "apr_adsp_up() not found"
s = s.replace(old_up,
              'static void apr_adsp_up(void)\n{\n\tpr_info("%s: Q6 is Up\\n", __func__);', 1)

# 3) probe 尾部挂兜底 (首次 3s, 之后由 work 自己每 2s 续探)
old_tail = "\treturn apr_debug_init();\n}\n\nstatic int apr_remove"
assert old_tail in s, "apr_probe() tail not found"
new_tail = ("\tINIT_DELAYED_WORK(&apr_fallback_populate_work,\n"
            "\t\t\tapr_fallback_populate);\n"
            "\tschedule_delayed_work(&apr_fallback_populate_work,\n"
            "\t\t\tmsecs_to_jiffies(3000));\n\n"
            "\treturn apr_debug_init();\n}\n\nstatic int apr_remove")
s = s.replace(old_tail, new_tail, 1)

assert s != orig, "no change applied"
open(path, "w", encoding="utf-8").write(s)
print("apr.c patched v3: q6-state-driven fallback populate + diagnostics")
