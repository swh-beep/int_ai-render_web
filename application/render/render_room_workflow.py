from typing import Any, Callable

from application.render.reference_preparation import prepare_render_references
from application.render.render_analysis_stage import run_render_analysis_stage
from application.render.render_audience_stage import run_render_audience_stage
from application.render.render_bootstrap_stage import run_render_bootstrap_stage
from application.render.render_empty_stage import run_render_empty_stage
from application.render.render_input_stage import run_render_input_stage
from application.render.render_postprocess_stage import run_render_postprocess_stage
from application.render.render_response_stage import build_render_response_payload, log_render_summary
from application.render.render_scale_stage import run_render_scale_stage
from application.render.render_variant_stage import run_render_variant_stage
from application.render.render_workflow_contracts import (
    RenderWorkflowDependencies,
    RenderWorkflowRequest,
)


def run_render_room_workflow(
    request: RenderWorkflowRequest,
    deps: RenderWorkflowDependencies,
) -> dict:
    summary_token = None
    try:
        bootstrap = run_render_bootstrap_stage(
            generate_unique_id=deps.runtime.generate_unique_id,
            time_now=deps.runtime.time_now,
            log_section=deps.runtime.log_section,
            summary_ref=deps.runtime.summary_ref,
        )
        unique_id = bootstrap.unique_id
        start_time = bootstrap.start_time
        summary = bootstrap.summary
        summary_token = bootstrap.summary_token

        audience_result = run_render_audience_stage(
            audience=request.audience,
            normalize_audience=deps.storage.normalize_audience,
            build_s3_prefix=deps.storage.build_s3_prefix,
        )
        aud = audience_result.audience
        enable_scale_check = audience_result.enable_scale_check
        prefix_main_user = audience_result.prefix_main_user
        prefix_main_empty = audience_result.prefix_main_empty
        prefix_main_rendered = audience_result.prefix_main_rendered
        prefix_customize = audience_result.prefix_customize

        input_result = run_render_input_stage(
            upload_file=request.file,
            unique_id=unique_id,
            time_now=deps.runtime.time_now,
            standardize_image=deps.storage.standardize_image,
        )
        timestamp = input_result.timestamp
        std_path = input_result.std_path

        empty_stage_result = run_render_empty_stage(
            std_path=std_path,
            unique_id=unique_id,
            start_time=start_time,
            generate_empty_room=deps.generation.generate_empty_room,
        )
        step1_img = empty_stage_result.step1_img
        step1_raw = empty_stage_result.step1_raw

        scale_stage_result = run_render_scale_stage(
            audience=aud,
            dimensions=request.dimensions,
            parse_room_dimensions_mm=deps.analysis.parse_room_dimensions_mm,
            room_dims_valid_fn=deps.analysis.room_dims_valid_fn,
            logger=deps.runtime.logger,
        )
        room_dims_parsed = scale_stage_result.room_dims_parsed
        enable_scale_guidance = scale_stage_result.enable_scale_guidance
        room_planes = scale_stage_result.room_planes
        wall_span_norm = scale_stage_result.wall_span_norm
        windows_present = scale_stage_result.windows_present
        room_analysis_text = scale_stage_result.room_analysis_text
        furniture_specs_text = scale_stage_result.furniture_specs_text
        furniture_specs_json = scale_stage_result.furniture_specs_json
        primary_item = scale_stage_result.primary_item
        scale_guide_path = scale_stage_result.scale_guide_path
        size_hierarchy = scale_stage_result.size_hierarchy
        full_analyzed_data = scale_stage_result.full_analyzed_data

        reference_selection = prepare_render_references(
            moodboard_items=request.moodboard_items,
            style=request.style,
            room=request.room,
            variant=request.variant,
            moodboard=request.moodboard,
            timestamp=timestamp,
            unique_id=unique_id,
            prefix_customize=prefix_customize,
            use_s3_moodboard=deps.runtime.use_s3_moodboard,
            materialize_input=deps.storage.materialize_input,
            resolve_image_url=deps.storage.resolve_image_url,
            build_item_target_key=deps.analysis.build_item_target_key,
            canonical_category=deps.analysis.canonical_category,
            find_s3_moodboard_key=deps.storage.find_s3_moodboard_key,
            s3_public_url=deps.storage.s3_public_url,
        )
        mb_url = reference_selection.mb_url
        ref_paths = reference_selection.ref_paths
        item_refs = reference_selection.item_refs

        analysis_result = run_render_analysis_stage(
            ref_paths=ref_paths,
            item_refs=item_refs,
            step1_img=step1_img,
            step1_raw=step1_raw,
            dimensions=request.dimensions,
            unique_id=unique_id,
            detect_furniture_boxes=deps.analysis.detect_furniture_boxes,
            canonical_category=deps.analysis.canonical_category,
            build_item_target_key=deps.analysis.build_item_target_key,
            analyze_room_structure=deps.analysis.analyze_room_structure,
            analyze_cropped_item=deps.analysis.analyze_cropped_item,
            normalize_dims_dict=deps.analysis.normalize_dims_dict,
            parse_object_dimensions_mm=deps.analysis.parse_object_dimensions_mm,
            build_furniture_specs_json=deps.analysis.build_furniture_specs_json,
            create_scale_guide_overlay_with_model=deps.analysis.create_scale_guide_overlay_with_model,
            match_aspect_to_target=deps.analysis.match_aspect_to_target,
            enable_scale_guidance=enable_scale_guidance,
            room_dims_parsed=room_dims_parsed,
            summary=summary,
            logger=deps.runtime.logger,
            log_brief=deps.runtime.log_brief,
            max_concurrency_analysis=deps.runtime.max_concurrency_analysis,
            cart_max_analysis_workers=deps.runtime.cart_max_analysis_workers,
        )
        windows_present = analysis_result.windows_present
        room_analysis_text = analysis_result.room_analysis_text
        furniture_specs_text = analysis_result.furniture_specs_text
        furniture_specs_json = analysis_result.furniture_specs_json
        full_analyzed_data = analysis_result.full_analyzed_data or []
        primary_item = analysis_result.primary_item
        scale_guide_path = analysis_result.scale_guide_path
        size_hierarchy = analysis_result.size_hierarchy

        if windows_present is None:
            windows_present = False
        deps.runtime.log_section("[Stage 2] 3 variations start (Specs Injection)")

        ref_input = ref_paths if len(ref_paths) > 1 else (ref_paths[0] if ref_paths else None)
        generated_results = run_render_variant_stage(
            step1_img=step1_img,
            style_prompt=deps.runtime.style_map.get(request.style, "Custom Moodboard Style"),
            ref_input=ref_input,
            unique_id=unique_id,
            furniture_specs_text=furniture_specs_text,
            furniture_specs_json=furniture_specs_json,
            dimensions=request.dimensions,
            placement=request.placement,
            scale_guide_path=scale_guide_path,
            primary_item=primary_item,
            room_dims_parsed=room_dims_parsed,
            wall_span_norm=wall_span_norm,
            size_hierarchy=size_hierarchy,
            start_time=start_time,
            room_planes=room_planes,
            windows_present=windows_present,
            room_analysis_text=room_analysis_text,
            enable_scale_check=enable_scale_check,
            generate_furnished_room=deps.generation.generate_furnished_room,
        )

        postprocess_result = run_render_postprocess_stage(
            generated_results=generated_results,
            full_analyzed_data=full_analyzed_data,
            audience=aud,
            rank_best_variant=deps.postprocess.rank_best_variant,
            refresh_item_boxes_from_main_render=deps.postprocess.refresh_item_boxes_from_main_render,
            attach_volume_ranks=deps.postprocess.attach_volume_ranks,
            volume_ranking_snapshot=deps.postprocess.volume_ranking_snapshot,
            logger=deps.runtime.logger,
            log_brief=deps.runtime.log_brief,
        )
        generated_results = postprocess_result.generated_results
        full_analyzed_data = postprocess_result.full_analyzed_data
        volume_ranking = postprocess_result.volume_ranking

        log_render_summary(summary, log_summary=deps.runtime.log_summary, logger=deps.runtime.logger)
        return build_render_response_payload(
            std_path=std_path,
            step1_img=step1_img,
            scale_guide_path=scale_guide_path,
            generated_results=generated_results,
            moodboard_url=mb_url,
            furniture_data=full_analyzed_data,
            volume_ranking=volume_ranking,
            prefix_main_user=prefix_main_user,
            prefix_main_empty=prefix_main_empty,
            prefix_main_rendered=prefix_main_rendered,
            resolve_image_url=deps.storage.resolve_image_url,
        )
    finally:
        deps.runtime.reset_summary_token(summary_token)
