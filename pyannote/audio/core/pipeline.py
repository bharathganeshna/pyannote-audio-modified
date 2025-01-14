# MIT License
#
# Copyright (c) 2021 CNRS
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import os
import warnings
from collections import OrderedDict
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from typing import Callable, Dict, List, Optional, Text, Union

import torch
import torch.nn as nn
import yaml
from huggingface_hub import hf_hub_download
from huggingface_hub.utils import RepositoryNotFoundError
from pyannote.core.utils.helper import get_class_by_name
from pyannote.database import FileFinder, ProtocolFile
from pyannote.pipeline import Pipeline as _Pipeline

from pyannote.audio import Audio, __version__
from pyannote.audio.core.inference import BaseInference
from pyannote.audio.core.io import AudioFile
from pyannote.audio.core.model import CACHE_DIR, Model
from pyannote.audio.utils.reproducibility import fix_reproducibility
from pyannote.audio.utils.version import check_version

PIPELINE_PARAMS_NAME = "config.yaml"

class Pipeline(_Pipeline):
    @classmethod
    def from_pretrained(cls, checkpoint_path: Union[Text, Path], hparams_file: Union[Text, Path] = None, cache_dir: Union[Path, Text] = CACHE_DIR) -> "Pipeline":
        """Load pretrained pipeline from a local path
        Parameters
        ----------
        checkpoint_path : Path or str
            Path to pipeline checkpoint, assumed to be a local file path.
        hparams_file: Path or str, optional
            Path to a hyperparameters file.
        cache_dir: Path or str, optional
            Path to model cache directory. Defaults to pyannote's CACHE_DIR when unset.
        Returns
        -------
        Pipeline
            An instance of the requested pipeline.
        """
        checkpoint_path = str(checkpoint_path)
        if not os.path.isfile(checkpoint_path):
            raise FileNotFoundError(f"Checkpoint file not found: {checkpoint_path}")
        config_yml = checkpoint_path
        with open(config_yml, "r") as fp:
            config = yaml.load(fp, Loader=yaml.SafeLoader)
        if "version" in config:
            check_version("pyannote.audio", config["version"], __version__, what="Pipeline")
        pipeline_name = config["pipeline"]["name"]
        Klass = get_class_by_name(pipeline_name, default_module_name="pyannote.pipeline.blocks")
        params = config["pipeline"].get("params", {})
        pipeline = Klass(**params)
        if "freeze" in config:
            params = config["freeze"]
            pipeline.freeze(params)
        if "params" in config:
            pipeline.instantiate(config["params"])
        if hparams_file is not None:
            pipeline.load_params(hparams_file)
        if "preprocessors" in config:
            preprocessors = {}
            for key, preprocessor in config.get("preprocessors", {}).items():
                if isinstance(preprocessor, dict):
                    Klass = get_class_by_name(preprocessor["name"], default_module_name="pyannote.audio")
                    params = preprocessor.get("params", {})
                    preprocessors[key] = Klass(**params)
                else:
                    try:
                        preprocessors[key] = FileFinder(database_yml=preprocessor)
                    except FileNotFoundError:
                        template = preprocessor
                        preprocessors[key] = template
            pipeline.preprocessors = preprocessors
        if "device" in config:
            device = torch.device(config["device"])
            try:
                pipeline.to(device)
            except RuntimeError as e:
                print(e)
        return pipeline

    def __init__(self):
        super().__init__()
        self._models: Dict[str, Model] = OrderedDict()
        self._inferences: Dict[str, BaseInference] = OrderedDict()

    def __getattr__(self, name):
        if "_models" in self.__dict__:
            _models = self.__dict__["_models"]
            if name in _models:
                return _models[name]
        if "_inferences" in self.__dict__:
            _inferences = self.__dict__["_inferences"]
            if name in _inferences:
                return _inferences[name]
        return super().__getattr__(name)

    def __setattr__(self, name, value):
        def remove_from(*dicts):
            for d in dicts:
                if name in d:
                    del d[name]
        _parameters = self.__dict__.get("_parameters")
        _instantiated = self.__dict__.get("_instantiated")
        _pipelines = self.__dict__.get("_pipelines")
        _models = self.__dict__.get("_models")
        _inferences = self.__dict__.get("_inferences")
        if isinstance(value, nn.Module):
            if _models is None:
                msg = "cannot assign models before Pipeline.__init__() call"
                raise AttributeError(msg)
            remove_from(self.__dict__, _inferences, _parameters, _instantiated, _pipelines)
            _models[name] = value
        elif isinstance(value, BaseInference):
            if _inferences is None:
                msg = "cannot assign inferences before Pipeline.__init__() call"
                raise AttributeError(msg)
            remove_from(self.__dict__, _models, _parameters, _instantiated, _pipelines)
            _inferences[name] = value
        else:
            super().__setattr__(name, value)

    def __delattr__(self, name):
        if name in self._models:
            del self._models[name]
        elif name in self._inferences:
            del self._inferences[name]
        else:
            super().__delattr__(name)

    @staticmethod
    def setup_hook(file: AudioFile, hook: Optional[Callable] = None) -> Callable:
        def noop(*args, **kwargs):
            return
        return partial(hook or noop, file=file)

    def default_parameters(self):
        raise NotImplementedError()

    def classes(self) -> Union[List, Iterator]:
        raise NotImplementedError()

    def __call__(self, file: AudioFile, **kwargs):
        fix_reproducibility(getattr(self, "device", torch.device("cpu")))
        if not self.instantiated:
            try:
                default_parameters = self.default_parameters()
            except NotImplementedError:
                raise RuntimeError("A pipeline must be instantiated with `pipeline.instantiate(parameters)` before it can be applied.")
            try:
                self.instantiate(default_parameters)
            except ValueError:
                raise RuntimeError("A pipeline must be instantiated with `pipeline.instantiate(parameters)` before it can be applied.")
            warnings.warn(f"The pipeline has been automatically instantiated with {default_parameters}.")
        file = Audio.validate_file(file)
        if hasattr(self, "preprocessors"):
            file = ProtocolFile(file, lazy=self.preprocessors)
        return self.apply(file, **kwargs)

    def to(self, device: torch.device):
        if not isinstance(device, torch.device):
            raise TypeError(f"`device` must be an instance of `torch.device`, got `{type(device).__name__}`")
        for _, pipeline in self._pipelines.items():
            if hasattr(pipeline, "to"):
                _ = pipeline.to(device)
        for _, model in self._models.items():
            _ = model.to(device)
        for _, inference in self._inferences.items():
            _ = inference.to(device)
        self.device = device
        return self
