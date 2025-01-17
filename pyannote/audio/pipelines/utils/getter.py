# The MIT License (MIT)
#
# Copyright (c) 2017- CNRS
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
import os
import itertools
from typing import Mapping, Optional, Text, Union

import torch
from torch_audiomentations.core.transforms_interface import BaseWaveformTransform
from torch_audiomentations.utils.config import from_dict as augmentation_from_dict

from pyannote.audio import Inference, Model

PipelineModel = Union[Model, Text, Mapping]

def get_model(model: Union[str, Mapping]):
    """Load a pretrained model strictly from local files.

    Parameters:
    model (Union[str, Mapping]): Local path to the model file or directory, or a dictionary specifying the path.

    Returns:
    torch.nn.Module: The loaded model.
    """
    if isinstance(model, str):
        # Assume model is a direct file path
        if os.path.isfile(model):
            return torch.load(model)
        else:
            # Check if the model path might refer to a directory with a 'model.pth' file
            model_path = os.path.join(model, 'model.pth')
            if os.path.isfile(model_path):
                return torch.load(model_path)
            else:
                raise FileNotFoundError(f"No model file found at path: {model} or within directory as 'model.pth'")

    elif isinstance(model, Mapping):
        # Handle dictionary input, expecting a 'checkpoint' key for the model path
        checkpoint = model.get("checkpoint")
        if checkpoint and os.path.isfile(checkpoint):
            return torch.load(checkpoint)
        else:
            raise FileNotFoundError(f"No model found at path specified by 'checkpoint': {checkpoint}")

    else:
        raise TypeError("Model input must be a string path or a dictionary with a 'checkpoint' key.")


    model.eval()
    return model


PipelineInference = Union[Inference, Model, Text, Mapping]


def get_inference(inference: PipelineInference) -> Inference:
    """Load inference

    Parameter
    ---------
    inference : Inference, Model, str, or dict
        When `Inference`, returns `inference` as is.
        When `Model`, wraps it in `Inference(model)`.
        When `str`, assumes that this is either the path to a checkpoint or the name of a
        pretrained model on Huggingface.co and loads with `Inference(checkpoint)`.
        When `dict`, loads with `Inference(**inference)`.

    Returns
    -------
    inference : Inference
        Inference.

    Examples
    --------
    >>> inference = get_inference("hbredin/VoiceActivityDetection-PyanNet-DIHARD")
    >>> inference = get_inference("/path/to/checkpoint.ckpt")
    >>> inference = get_inference({"model": "hbredin/VoiceActivityDetection-PyanNet-DIHARD",
    ...                            "window": "sliding"})

    See also
    --------
    pyannote.audio.core.inference.Inference

    """

    if isinstance(inference, Inference):
        return inference

    if isinstance(inference, (Model, Text)):
        return Inference(inference)

    if isinstance(inference, Mapping):
        return Inference(**inference)

    raise TypeError(
        f"Unsupported type ({type(inference)}) for loading inference: "
        f"expected `Model`, `str` or `dict`."
    )


PipelineAugmentation = Union[BaseWaveformTransform, Mapping]


def get_augmentation(augmentation: PipelineAugmentation) -> BaseWaveformTransform:
    """Load augmentation

    Parameter
    ---------
    augmentation : BaseWaveformTransform, or dict
        When `BaseWaveformTransform`, returns `augmentation` as is.
        When `dict`, loads with `torch_audiomentations`'s `from_config` utility function.

    Returns
    -------
    augmentation : BaseWaveformTransform
        Augmentation.
    """

    if augmentation is None:
        return None

    if isinstance(augmentation, BaseWaveformTransform):
        return augmentation

    if isinstance(augmentation, Mapping):
        return augmentation_from_dict(augmentation)

    raise TypeError(
        f"Unsupported type ({type(augmentation)}) for loading augmentation: "
        f"expected `BaseWaveformTransform`, or `dict`."
    )


def get_devices(needs: Optional[int] = None):
    """Get devices that can be used by the pipeline

    Parameters
    ----------
    needs : int, optional
        Number of devices needed by the pipeline

    Returns
    -------
    devices : list of torch.device
        List of available devices.
        When `needs` is provided, returns that many devices.
    """

    num_gpus = torch.cuda.device_count()

    if num_gpus == 0:
        devices = [torch.device("cpu")]
        if needs is None:
            return devices
        return devices * needs

    devices = [torch.device(f"cuda:{index:d}") for index in range(num_gpus)]
    if needs is None:
        return devices
    return [device for _, device in zip(range(needs), itertools.cycle(devices))]
