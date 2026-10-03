"""
CLI 应用模块
Click-based command-line interface for MediaScraper.
"""

import logging
import os
import sys

import click
import yaml

# 确保项目根目录在 sys.path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.config import load_config, save_config, validate_config, DEFAULT_CONFIG
from core.logging_setup import setup_logging
from core.orchestrator import Pipeline
from core.progress import format_progress
from core.parser import parse_filename

logger = logging.getLogger(__name__)


@click.group()
@click.option("--config", "-c", default=None, help="配置文件路径")
@click.pass_context
def cli(ctx, config):
    """MediaScraper -- 家庭影院媒体刮削与重命名工具"""
    ctx.ensure_object(dict)
    cfg = load_config(config)
    ctx.obj["config"] = cfg
    ctx.obj["config_path"] = config
    setup_logging(
        cfg.get("logging", {}).get("level", "INFO"),
        cfg.get("logging", {}).get("log_to_file", True),
    )


@cli.command()
@click.argument("directory", type=click.Path(exists=True))
@click.option("--dry-run/--apply", default=True, help="预览模式 / 实际执行")
@click.option("--output", "-o", default=None, help="CSV 输出路径")
@click.pass_context
def scan(ctx, directory, dry_run, output):
    """扫描视频目录并解析文件名（不联网刮削）。"""
    config = ctx.obj["config"]
    pipeline = Pipeline(config)

    def on_progress(info):
        click.echo(format_progress(info))

    pipeline.set_progress_callback(on_progress)

    csv_path = output or (
        os.path.join(directory, "rename_plan.csv") if dry_run else None
    )

    plans = pipeline.run_dry(directory, output_csv=csv_path, do_scrape=False)
    summary = pipeline.get_summary()

    click.echo()
    click.echo("=" * 60)
    click.echo(f" 扫描完成!")
    click.echo(f" 视频文件: {summary['total_files']} 个")
    click.echo(f" 重命名计划: {summary['rename_plans']} 个")
    click.echo(f" 类型分布: {summary['type_distribution']}")
    if csv_path:
        click.echo(f" CSV 已导出: {csv_path}")
    click.echo("=" * 60)

    if dry_run and plans:
        click.echo()
        click.echo("重命名预览 (前 10 条):")
        click.echo("-" * 60)
        for i, plan in enumerate(plans[:10], 1):
            src = os.path.basename(plan.source_path)
            tgt = os.path.basename(plan.target_path)
            click.echo(f"  {i:3d}. {src}")
            click.echo(f"       -> {tgt}")
        if len(plans) > 10:
            click.echo(f"  ... 还有 {len(plans) - 10} 条，详见 CSV")


@cli.command()
@click.argument("directory", type=click.Path(exists=True))
@click.option("--output", "-o", default=None, help="CSV 输出路径")
@click.option("--apply", "do_apply", is_flag=True, default=False,
              help="实际执行重命名和移动（默认 dry-run 仅预览）")
@click.pass_context
def scrape(ctx, directory, output, do_apply):
    """完整刮削：扫描 -> 解析 -> 搜索元数据 -> 写入 NFO/JSON/封面 -> 打包文件夹。

    默认 dry-run 模式：仅预览，不移动文件。
    加 --apply 实际执行重命名和文件移动。
    """
    config = ctx.obj["config"]
    pipeline = Pipeline(config)

    def on_progress(info):
        click.echo(format_progress(info))

    pipeline.set_progress_callback(on_progress)

    csv_path = output or os.path.join(directory, "scrape_plan.csv")

    if do_apply:
        click.echo("[!] 实际执行模式 - 视频文件将被移动/重命名")
        click.echo()
        results = pipeline.run_apply(directory, output_csv=csv_path, do_scrape=True)
        summary = pipeline.get_summary()

        moved = [r for r in results if r["action"] == "move"]
        errors = [r for r in results if r["action"] == "error"]

        click.echo()
        click.echo("=" * 60)
        click.echo(f" 刮削执行完成!")
        click.echo(f" 视频文件: {summary['total_files']} 个")
        click.echo(f" 刮削成功: {summary['scraped_count']} 个")
        click.echo(f" 已移动: {len(moved)} 个文件")
        if errors:
            click.echo(f" 失败: {len(errors)} 个")
        click.echo(f" 类型分布: {summary['type_distribution']}")
        if csv_path:
            click.echo(f" CSV 已导出: {csv_path}")
        if results:
            # 找到回滚日志路径
            for f in os.listdir(directory):
                if f.startswith("rollback_") and f.endswith(".json"):
                    click.echo(f" 回滚日志: {os.path.join(directory, f)}")
        click.echo("=" * 60)

        if moved:
            click.echo()
            click.echo("已处理的文件:")
            click.echo("-" * 60)
            for r in moved[:20]:
                click.echo(f"  {os.path.basename(r['source'])}")
                click.echo(f"    -> {os.path.basename(r['target'])}")
            if len(moved) > 20:
                click.echo(f"  ... 还有 {len(moved) - 20} 个")
    else:
        click.echo("[*] Dry-run 模式 - 仅预览，不移动文件（加 --apply 执行）")
        click.echo()
        plans = pipeline.run_dry(directory, output_csv=csv_path, do_scrape=True)
        summary = pipeline.get_summary()

        click.echo()
        click.echo("=" * 60)
        click.echo(f" 刮削完成!")
        click.echo(f" 视频文件: {summary['total_files']} 个")
        click.echo(f" 刮削成功: {summary['scraped_count']} 个")
        click.echo(f" 重命名计划: {summary['rename_plans']} 个")
        click.echo(f" 类型分布: {summary['type_distribution']}")
        if csv_path:
            click.echo(f" CSV 已导出: {csv_path}")
        click.echo("=" * 60)

        if plans:
            click.echo()
            click.echo("刮削结果预览 (前 10 条):")
            click.echo("-" * 60)
            for i, plan in enumerate(plans[:10], 1):
                src = os.path.basename(plan.source_path)
                meta = pipeline.scraped_metadata[i - 1] if i - 1 < len(pipeline.scraped_metadata) else None
                click.echo(f"  {i:3d}. {src}")
                if meta:
                    actors = ", ".join(a.name for a in meta.actors[:3])
                    click.echo(f"       标题: {meta.title} ({meta.year})")
                    click.echo(f"       评分: {meta.rating}  来源: {meta.source_provider}")
                    if actors:
                        click.echo(f"       演员: {actors}")
                else:
                    click.echo(f"       [无刮削结果]")
                click.echo(f"       -> {os.path.basename(plan.target_path)}")
            if len(plans) > 10:
                click.echo(f"  ... 还有 {len(plans) - 10} 条，详见 CSV")


@cli.command()
@click.pass_context
def config(ctx):
    """显示当前配置。"""
    cfg = ctx.obj["config"]
    click.echo(yaml.dump(cfg, allow_unicode=True, default_flow_style=False, sort_keys=False))

    errors = validate_config(cfg)
    if errors:
        click.echo("配置错误:")
        for e in errors:
            click.echo(f"  X {e}")
    else:
        click.echo("OK 配置验证通过")


@cli.command()
@click.pass_context
def providers(ctx):
    """列出已注册的 Provider。"""
    from providers import get_all_providers
    all_providers = get_all_providers()
    if not all_providers:
        click.echo("暂无已注册的 Provider")
        return
    click.echo(f"已注册 {len(all_providers)} 个 Provider:")
    for name, cls in all_providers.items():
        inst = cls()
        click.echo(f"  [{name}] {inst.display_name}")
        click.echo(f"    支持类型: {', '.join(inst.supported_types)}")


@cli.command()
@click.argument("log_file", type=click.Path(exists=True))
def rollback(log_file):
    """回滚重命名操作（从 rollback_*.json 日志恢复）。"""
    results = Pipeline.rollback(log_file)
    ok = [r for r in results if r.get("status") == "ok"]
    skipped = [r for r in results if r["action"] == "skip"]
    errors = [r for r in results if r.get("status") == "error"]

    click.echo(f"回滚完成:")
    click.echo(f"  已恢复: {len(ok)} 个")
    click.echo(f"  跳过: {len(skipped)} 个")
    if errors:
        click.echo(f"  失败: {len(errors)} 个")


@cli.command()
@click.pass_context
def gui(ctx):
    """启动图形界面。"""
    try:
        from gui import MainWindow
        from PySide6.QtWidgets import QApplication
        import sys

        app = QApplication.instance() or QApplication(sys.argv)
        config = ctx.obj.get("config", {})
        config_path = ctx.obj.get("config_path")
        window = MainWindow(config=config, config_path=config_path)
        window.show()
        sys.exit(app.exec())
    except ImportError as e:
        click.echo(f"GUI 依赖缺失，请安装 PySide6: pip install PySide6")
        click.echo(f"错误: {e}")
    except Exception as e:
        click.echo(f"启动 GUI 失败: {e}")
    for r in results:
        if r["action"] == "rollback" and r.get("status") == "ok":
            click.echo(f"  ✓ {os.path.basename(r['from'])} -> {os.path.basename(r['to'])}")


@cli.command()
def version():
    """显示版本号。"""
    from version import __version__
    click.echo(f"MediaScraper {__version__}")


def main():
    """CLI 入口。"""
    cli(obj={})


if __name__ == "__main__":
    main()
