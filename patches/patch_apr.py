#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apr.c patch

问题: vendor 音频模块 (snd-soc-sdm845 / swr_wcd_ctrl / snd_soc_wcd9xxx ...) 全部
加载成功, 但 refcount 恒为 0, 声卡不注册 (/dev/snd 只有 timer) -> 无声 + 视频加速。

机理: techpack/audio/ipc/apr.c 只有在收到 ADSP up 事件后才会
of_platform_populate() 创建自己的 DT 子设备 (sound-tavil 等)。
该事件走 audio_notifier 的 PDR 通道:
  audio_pdr.c -> service-locator -> QMI over ipc_router -> ADSP "avs/audio"
事件链一旦没打通, 子设备永不创建, vendor machine driver
(compatible = qcom,sdm845-asoc-snd-tavil) 就没有 device 可绑, 声卡注册不了。

本补丁:
  1) apr_add_child_devices(): 打印 dev_info, 供 dmesg 判断事件是否到达;
  2) 新增 apr_fallback_populate(): 20s 后强制 populate 一次 (of_platform_populate
     幂等; 此时 ADSP 早已 up, 子设备驱动 probe 可与 ADSP 正常通信), 作为事件链失效兜底;
  3) apr_adsp_up(): pr_debug -> pr_info, 事件到达与否可直接在 dmesg 看到。
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

# 1) apr_add_child_devices 加日志 + 追加兜底函数
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

static void apr_fallback_populate(struct work_struct *work)
{
\tif (!apr_priv || !apr_priv->dev)
\t\treturn;

\tdev_info(apr_priv->dev, "%s: fallback populate child devices\\n",
\t\t\t__func__);
\tof_platform_populate(apr_priv->dev->of_node, NULL, NULL, apr_priv->dev);
}'''
s = s[:m.start()] + new_fn + s[m.end():]

# 2) apr_adsp_up 可见化
old_up = 'static void apr_adsp_up(void)\n{\n\tpr_debug("%s: Q6 is Up\\n", __func__);'
assert old_up in s, "apr_adsp_up() not found"
s = s.replace(old_up,
              'static void apr_adsp_up(void)\n{\n\tpr_info("%s: Q6 is Up\\n", __func__);', 1)

# 3) probe 尾部挂兜底
old_tail = "\treturn apr_debug_init();\n}\n\nstatic int apr_remove"
assert old_tail in s, "apr_probe() tail not found"
new_tail = ("\tINIT_DELAYED_WORK(&apr_fallback_populate_work,\n"
            "\t\t\tapr_fallback_populate);\n"
            "\tschedule_delayed_work(&apr_fallback_populate_work,\n"
            "\t\t\tmsecs_to_jiffies(20000));\n\n"
            "\treturn apr_debug_init();\n}\n\nstatic int apr_remove")
s = s.replace(old_tail, new_tail, 1)

assert s != orig, "no change applied"
open(path, "w", encoding="utf-8").write(s)
print("apr.c patched: fallback populate + diagnostics")
