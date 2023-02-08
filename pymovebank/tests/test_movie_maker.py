import pytest

from pathlib import Path
from pymovebank.panel_utils import make_mp4_from_frames
from pymovebank.tests.conftest import test_frames_dir, test_frames_dir_weird, test_output_dir, test_output_dir_weird


@pytest.mark.parametrize('frames_dir,output_dir',
                         ([test_frames_dir, test_output_dir], [test_frames_dir_weird, test_output_dir_weird]))
def test_that_movie_maker_runs(install_test_data, make_test_frame_dirs, frames_dir, output_dir):

    print(f"frames dir: {frames_dir}")

    output_dir.mkdir(exist_ok=True)

    output_file = output_dir / "output.mp4"
    frame_rate=1

    # Run movie maker
    make_mp4_from_frames(str(frames_dir), str(output_file), frame_rate)

    # Check that the output file exists
    assert Path(output_file).exists()