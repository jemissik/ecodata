from pathlib import Path

import pytest

from ecodata.panel_utils import make_mp4_from_frames
from ecodata.tests.conftest import (
    test_frames_dir,
    test_frames_dir_weird,
    test_output_dir,
    test_output_dir_weird,
)


@pytest.mark.parametrize(
    "frames_dir,output_dir",
    ([test_frames_dir, test_output_dir], [test_frames_dir_weird, test_output_dir_weird], [test_frames_dir, None]),
)
def test_that_movie_maker_runs(install_test_data, make_test_frame_dirs, frames_dir, output_dir):

    print(f"frames dir: {frames_dir!r}")
    print(frames_dir.exists())

    if output_dir is None:
        output_file = "output.mp4"
    else:
        output_dir.mkdir(exist_ok=True)
        output_file = output_dir / "output.mp4"

    frame_rate = 1

    # Run movie maker
    output_mp4 = make_mp4_from_frames(str(frames_dir), str(output_file), frame_rate)

    # Check that the output file exists
    assert output_mp4.exists()
