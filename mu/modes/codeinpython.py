"""
This mode is based on Pygame Zero mode.

Copyright (c) 2015-2017 Nicholas H.Tollervey and others (see the AUTHORS file).

Mode is dedicated to cooperate with CodeInPython devices. In addition, it deliver
very convinient solution to use by many users. Each of them can keep his own set of
lessons, add new one from CodeInPython resurces, or from private collection.

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
import os
import sys
import re
import shutil
import logging
import urllib.parse
import zipfile
import importlib
from datetime import datetime
import xml.etree.ElementTree as ET
from distutils.sysconfig import get_python_lib

from mu.modes.base import get_default_workspace
from mu.modes.api import PYTHON3_APIS, SHARED_APIS, PI_APIS, PYGAMEZERO_APIS
from mu.modes.pygamezero import PyGameZeroMode
from mu.resources import load_icon
from ..virtual_environment import venv
from .. import settings

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import QRegExp
from PyQt5.QtGui import QRegExpValidator

from mu.contrib.codeinpython_ui_users import Ui_dialog as Ui_Dialog_Users
from mu.contrib.codeinpython_ui_tab_setup import Ui_Form as Ui_Tab_Settings

logger = logging.getLogger(__name__)


def path_for_codeinpython_add(path_to_add=""):
    """
    Adding specific path to sys.path. In this way will be simpler to include
    modules from CodeInPython. It's needed because user work will be not in fixed
    place, but in different folders. So, relative import will not work
    """
    if path_to_add == "":
        if get_default_workspace() not in sys.path:
            sys.path.append(get_default_workspace())
    else:
        path_cleaned = path_clean(path_to_add)
        if path_cleaned not in sys.path:
            sys.path.append(path_cleaned)


def recursive_overwrite(src, dest, ignore=None):
    if os.path.isdir(src):
        if not os.path.isdir(dest):
            os.makedirs(dest)
        files = os.listdir(src)
        if ignore is not None:
            ignored = ignore(src, files)
        else:
            ignored = set()
        for f in files:
            if f not in ignored:
                recursive_overwrite(
                    os.path.join(src, f), os.path.join(dest, f), ignore
                )
    else:
        try:
            if not os.path.isdir(os.path.dirname(dest)):
                os.makedirs(os.path.dirname(dest))
            shutil.copy2(src, dest)
        except shutil.SameFileError:
            src.replace(dest)


def path_clean(path_to_clean="", lowercase=False, real=True):
    if path_to_clean == "":
        return ""
    clean_path = os.path.normpath(path_to_clean)
    if lowercase:
        clean_path = os.path.normcase(clean_path)
    clean_path = os.path.expanduser(clean_path)
    if real:
        clean_path = os.path.realpath(clean_path)
    return clean_path


def xml_get_with_lang(child, name, lang=None, default="en", separator=""):
    result = None
    lang_best_fit = None

    # first, find best suite language
    for element in child:
        if element.tag == name:
            which_lang = element.get("lang")
            if which_lang == lang:  # ok, we got best fit
                lang_best_fit = lang
                break
            if which_lang == default:
                # could be better, we will search for other language
                lang_best_fit = default
                continue
            if lang_best_fit is None:
                lang_best_fit = which_lang

    # now, get all lines with choosen language
    for element in child:
        if element.tag == name:
            which_lang = element.get("lang")
            if which_lang == lang_best_fit:
                if result is None:
                    result = element.text
                else:
                    result = "{}{}{}".format(result, separator, element.text)
    return result


def remove_empty_folders(path_abs, files_to_ignore=[]):
    """removing all empty folders (tree).
    Files_to_ignore: here can be added files which will not interrupt
    deleting folder (still folder will be treated as empty)"""
    walk = list(os.walk(path_abs))
    for path, folders, files in walk[::-1]:
        if set(os.listdir(path)).issubset(set(files_to_ignore)):
            shutil.rmtree(path, ignore_errors=True)


class Dialog_GetText:
    """
    Popup window to input one text line. There is also possibility to
    restrict inputted chars to some set (i.e. only letters)
    """

    def __init__(self, comment=" ", validator="", initial_value=""):
        super().__init__()
        self.comment = comment
        self.validator = validator
        self.initial_value = initial_value
        self.initUI()

    def initUI(self):
        self.win = QtWidgets.QDialog()
        self.win.setWindowTitle(self.comment)

        self.edit_bar = QtWidgets.QLineEdit(self.initial_value)
        font = QtGui.QFont()
        font.setPointSize(20)
        self.edit_bar.setFont(font)
        if self.validator != "":
            self.edit_bar.setValidator(
                QRegExpValidator(QRegExp(self.validator))
            )  # i.e.: r"[0-9A-Za-z_+=-]{20}")))
        self.edit_bar.returnPressed.connect(self.win.accept)

        self.box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self.box.accepted.connect(self.win.accept)
        self.box.rejected.connect(self.win.reject)

        flow = QtWidgets.QFormLayout()
        flow.addRow("", self.edit_bar)
        flow.addRow("", self.box)
        self.win.setLayout(flow)

    def get(self):
        result = self.win.exec()
        return bool(result), self.edit_bar.text()


class CodeInPython_Settings:
    """
    Class with set of tools needed by this mode.
    """

    # file with stored path for CodeInPython stuff (to solve problem PYTHONPATH)
    SEARCH_PATH_FILENAME = "codeinpython_path.pth"
    # where on internet are placed updates for CodeInPython?
    # URL_FOR_UPDATE_FILE = "http://www.tinyapi.eu/codeinpython/"
    URL_FOR_UPDATE_FILE = "http://www.tinyapi.eu/codeinpython_demo/"
    ZIP_FILENAME = "codeinpython_update.zip"
    CONFIG_FILENAME = "codeinpython_config.xml"
    EXAMPLE_DESC_FILENAME = "description.xml"

    SUBPATH_MU_CODE = get_default_workspace()
    SUBPATH_MAIN = os.path.normpath("codeinpython_env")
    SUBPATH_WORKSPACE = os.path.normpath("workspace")
    SUBPATH_ARCHIVE = os.path.normpath("archive")
    SUBPATH_EXAMPLES = os.path.normpath("examples")
    SUBPATH_CUSTOM_EXAMPLES = os.path.normpath("custom_examples")
    SUBPATH_PRIVATE_EXAMPLES = os.path.normpath("private")
    SUBPATH_TRASH = os.path.normpath("trash")
    SUBPATH_TEMP = os.path.normpath("temp")
    SUBPATH_LIBRARIES = os.path.normpath("code_lib")
    SUBPATH_MU_MODULE = os.path.normpath("mu_modules")
    SUBPATH_FIRMWARE = os.path.normpath("firmware")
    DEVICES_MODULE_NAME = "devices"

    def __init__(self):
        self.object_to_update = []
        self.settings_from_file = {"class_name": "my_school"}
        self.subpath_class = os.path.normpath(
            self.settings_from_file["class_name"]
        )
        self.user_name = ""
        self.language = "pl"

        settings.settings.init()
        settings.settings.register_for_autosave()
        self.load_settings()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.save_settings()

    def __del__(self):
        self.save_settings()

    def load_settings(self):
        temp_settings = settings.settings.get("codeinpython")
        if temp_settings:
            self.settings_from_file.update(temp_settings)

    def save_settings(self):
        settings.settings.update({"codeinpython": self.settings_from_file})
        settings.settings.save()

    def put_setting(self, key, value):
        self.settings_from_file[key] = value
        self.update_all_panels_now()

    def get_setting(self, name=""):
        if name != "" and name in self.settings_from_file:
            return self.settings_from_file.get(name)
        else:
            return None

    def register_panels_to_update(self, ptr):
        self.object_to_update.append(ptr)

    def update_all_panels_now(self):
        for element in self.object_to_update:
            try:
                element.update()
            except Exception as err:
                label_text = _("This method cause error")
                label_text += ':\n{}: "{}"'.format(
                    type(err).__name__, str(err)
                )
                logger.error(label_text)

    def valid_path(self, path_input, allow_main):
        path_limit = self.__path_create("main")
        path_to_check = path_clean(path_input)
        if allow_main:
            result = path_to_check.startswith(path_limit)
        else:
            result = (os.path.split(path_to_check)[0]).startswith(path_limit)
        if not result:
            logger.error(
                _(
                    "Unauthorized attempt to operate outside "
                    "CodeInPython folder tree! Hacking?"
                )
            )
        return result

    def path_get(self, place="", *parameters):
        path_created = self.__path_create(place, *parameters)
        if self.valid_path(path_created, True):
            return path_created
        else:
            return ""

    def __path_create(self, place="", *parameters):
        # first, we will validate names of folders and files.
        params_separate = []
        for element in parameters:
            if element is None:
                continue
            element = os.path.normpath(element)
            elements = element.split(os.sep)
            params_separate += elements

        params = []
        for element in params_separate:
            s = str(element).strip().replace(" ", "_")
            s = re.sub(r"(?u)[^-\w.]", "", s)
            params.append(s)

        path_struct = {
            "main": (self.SUBPATH_MU_CODE, self.SUBPATH_MAIN, *params),
            "workspace": (
                self.SUBPATH_MU_CODE,
                self.SUBPATH_MAIN,
                self.SUBPATH_WORKSPACE,
                *params,
            ),
            "class": (
                self.SUBPATH_MU_CODE,
                self.SUBPATH_MAIN,
                self.SUBPATH_WORKSPACE,
                self.subpath_class,
                *params,
            ),
            "student": (
                self.SUBPATH_MU_CODE,
                self.SUBPATH_MAIN,
                self.SUBPATH_WORKSPACE,
                self.subpath_class,
                self.user_name,
                *params,
            ),
            "student file": (
                self.SUBPATH_MU_CODE,
                self.SUBPATH_MAIN,
                self.SUBPATH_WORKSPACE,
                self.subpath_class,
                self.user_name,
                *params,
                "append py file",
            ),
            "student private": (
                self.SUBPATH_MU_CODE,
                self.SUBPATH_MAIN,
                self.SUBPATH_WORKSPACE,
                self.subpath_class,
                self.user_name,
                self.SUBPATH_PRIVATE_EXAMPLES,
                *params,
            ),
            "student private file": (
                self.SUBPATH_MU_CODE,
                self.SUBPATH_MAIN,
                self.SUBPATH_WORKSPACE,
                self.subpath_class,
                self.user_name,
                self.SUBPATH_PRIVATE_EXAMPLES,
                *params,
                "append py file",
            ),
            "examples": (
                self.SUBPATH_MU_CODE,
                self.SUBPATH_MAIN,
                self.SUBPATH_EXAMPLES,
                *params,
            ),
            "custom examples": (
                self.SUBPATH_MU_CODE,
                self.SUBPATH_MAIN,
                self.SUBPATH_CUSTOM_EXAMPLES,
                *params,
            ),
            "zip install": (self.SUBPATH_MU_CODE, self.SUBPATH_MAIN, *params),
            "zip install default file": (
                self.SUBPATH_MU_CODE,
                self.SUBPATH_MAIN,
                self.ZIP_FILENAME,
            ),
            "zip_archive": (
                self.SUBPATH_MU_CODE,
                self.SUBPATH_MAIN,
                self.SUBPATH_ARCHIVE,
                *params,
            ),
            "zip archive file": (
                self.SUBPATH_MU_CODE,
                self.SUBPATH_MAIN,
                self.SUBPATH_ARCHIVE,
                self.ZIP_FILENAME,
            ),
            "temp": (
                self.SUBPATH_MU_CODE,
                self.SUBPATH_MAIN,
                self.SUBPATH_TEMP,
                *params,
            ),
            "libraries": (
                self.SUBPATH_MU_CODE,
                self.SUBPATH_MAIN,
                self.SUBPATH_LIBRARIES,
                *params,
            ),
            "firmware": (
                self.SUBPATH_MU_CODE,
                self.SUBPATH_MAIN,
                self.SUBPATH_FIRMWARE,
                *params,
            ),
            "mu_modules": (
                self.SUBPATH_MU_CODE,
                self.SUBPATH_MAIN,
                self.SUBPATH_MU_MODULE,
                *params,
            ),
        }

        path = ""
        path_subelements = path_struct.get(place)
        if path_subelements is not None:
            for element in path_subelements:
                if element == "append py file":
                    path = os.path.join(path, os.path.split(path)[1] + ".py")
                else:
                    path = os.path.join(path, element)
            return path_clean(path)

        raise ValueError(
            "Can't find method to assembly path for command: ", place
        )

    def show_comparision_of_zip_files(self, source=""):
        if source == "":
            source_path = self.path_get("zip install default file")
        else:
            source_path = path_clean(source)

        if self.zip_check_content(source_path):
            new_valid, new_version = self.zip_get_version(source_path)
            present_valid, present_version = self.zip_get_version(
                self.path_get("zip archive file")
            )
            if new_valid:
                label_text = _(
                    "Present software: {}, date: {}\n"
                    "New software: {}, date: {}\n\n"
                    "{}\n\n"
                    "Do you wish to install it?".format(
                        present_version["release"],
                        present_version["date"],
                        new_version["release"],
                        new_version["date"],
                        new_version["description"],
                    )
                )
                question = QtWidgets.QMessageBox.question(
                    None,
                    _("Question"),
                    label_text,
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                )
                if question == QtWidgets.QMessageBox.Yes:
                    return True
        else:
            QtWidgets.QMessageBox.warning(
                None,
                _("Error"),
                "This is not proper archive from CodeInPython",
                QtWidgets.QMessageBox.Ok,
            )
        return False

    def update_data_from_zip(self, source=""):
        """
        Function will update all data from chosen source path.
        Information about what to delete, what to copy, etc.
        are read from xml file, stored in same zip.
        """

        # this variable is for testing. If isn't empty, then files will be
        # saved in subfolder.
        # In this way we will not overwrite present, current data
        # In release mode it should be ""
        destination = ""
        destinantion_path = self.path_get("main", destination)
        if source == "":
            zip_file_path = self.path_get("zip install default file")
        else:
            zip_file_path = path_clean(source)

        temp_path = self.path_get("temp")
        shutil.rmtree(temp_path, ignore_errors=True)

        if not self.zip_check_content(zip_file_path):
            QtWidgets.QMessageBox.warning(
                None,
                _("Info"),
                _("Invalid data in zip file"),
                QtWidgets.QMessageBox.Ok,
            )
            return

        if not os.path.exists(destinantion_path):
            os.makedirs(destinantion_path)

        list_of_errors = ""
        try:
            with zipfile.ZipFile(zip_file_path, "r") as zip_ref:
                zip_ref.extractall(temp_path)
                with zip_ref.open(self.CONFIG_FILENAME, "r") as config:
                    tree = ET.parse(config)
                    root = tree.getroot()
                    for child in root:
                        if child.tag == "update":
                            for element in child:
                                if element.tag == "remove":
                                    path_to_remove = self.path_get(
                                        "main", destination, element.text
                                    )
                                    if self.valid_path(path_to_remove, False):
                                        if os.path.isdir(path_to_remove):
                                            shutil.rmtree(
                                                path_to_remove,
                                                ignore_errors=True,
                                            )
                                        else:
                                            if os.path.isfile(path_to_remove):
                                                os.remove(path_to_remove)
                                    else:
                                        list_of_errors += (
                                            "- removing: {}\n".format(
                                                element.text
                                            )
                                        )
                            # it has to be done as two loops
                            for element in child:
                                if element.tag == "copy":
                                    src = self.path_get("temp", element.text)
                                    dest = self.path_get(
                                        "main", destination, element.text
                                    )
                                    if not os.path.exists(src):
                                        list_of_errors += (
                                            "- copying: {}\n".format(
                                                element.text
                                            )
                                        )
                                        continue
                                    recursive_overwrite(src, dest)
            if list_of_errors != "":
                list_of_errors = (
                    _("List of installation errors:\n") + list_of_errors
                )
                QtWidgets.QMessageBox.warning(
                    None, _("Error"), list_of_errors, QtWidgets.QMessageBox.Ok
                )
            else:
                archive_file_path = self.path_get("zip archive file")
                if not os.path.exists(
                    archive_file_path
                ) or not os.path.samefile(zip_file_path, archive_file_path):
                    shutil.copy2(zip_file_path, archive_file_path)
            return True
        except Exception as err:
            label_text = _("Cannot install from this zip file")
            label_text += ':\n{}: "{}"'.format(type(err).__name__, str(err))
            logger.warning(label_text)
            QtWidgets.QMessageBox.warning(
                None, _("Info"), label_text, QtWidgets.QMessageBox.Ok
            )
            return False
        finally:
            if zip_file_path == self.path_get("zip install default file"):
                os.remove(zip_file_path)
            shutil.rmtree(temp_path, ignore_errors=True)

    def zip_get_version(self, source):
        if os.path.isfile(source):
            if os.path.splitext(source)[1] == ".zip":
                with zipfile.ZipFile(source, "r") as zip_ref:
                    if self.CONFIG_FILENAME in zip_ref.namelist():
                        with zip_ref.open(self.CONFIG_FILENAME, "r") as config:
                            return self.xml_get_version(config)
        return self.xml_get_version("")

    def xml_get_version(self, source):
        result = {"release": "----", "date": "----", "description": "----"}
        if source == "":
            return False, result

        tree = ET.parse(source)
        root = tree.getroot()

        for child in root:
            if child.tag == "release":
                result["release"] = child.text
            if child.tag == "release_date":
                result["date"] = child.text

        result["description"] = xml_get_with_lang(
            root, "description", self.language, separator="\n"
        )
        return True, result

    def zip_check_content(self, input_path=""):
        """
        Here is checked, if it is file, zip file, zip with proper content
        In future, will be added procedures to validate all files inside
        """
        if input_path != "":
            src = path_clean(input_path)
        else:
            src = self.path_get("zip install default file")

        if not os.path.isfile(src):
            return False

        if zipfile.is_zipfile(src):
            valid, _ = self.zip_get_version(src)
            if valid:
                return True
            else:
                return False
        else:
            logger.warning(_("This does not seems to be a valid zip file."))
            return False


cip_settings = CodeInPython_Settings()


class CodeInPython(PyGameZeroMode):
    """
    Represents the functionality required by the CodeInPython mode
    """

    name = _("CodeInPython")
    short_name = "codeinpython"
    description = _("Have fun in the world of CodeInPython")
    icon = "codeinpython"
    runner = None
    builtins = [
        "clock",
        "music",
        "Actor",
        "keyboard",
        "animate",
        "Rect",
        "ZRect",
        "images",
        "sounds",
        "mouse",
        "keys",
        "keymods",
        "exit",
        "screen",
    ]

    def __init__(self, editor, view):
        super().__init__(editor, view)

    def actions(self):
        """
        Return an ordered list of actions provided by this module. An action
        is a name (also used to identify the icon) , description, and handler.
        """
        return [
            {
                "name": "run",
                "display_name": _("Run"),
                "description": _("Run your CodeInPython program."),
                "handler": self.play_toggle,
                "shortcut": "F5",
            },
            {
                "name": "user",
                "display_name": cip_settings.user_name,
                "description": _("Current user"),
                "handler": self.user_dialog,
                "shortcut": "Ctrl+Shift+U",
            },
            {
                "name": "codeinpython_pad",
                "display_name": _("Devices"),
                "description": _("Devices from CodeInPython"),
                "handler": self.devices_dialog,
                "shortcut": "Ctrl+Shift+G",
            },
        ]

    def activate(self):
        pass

    def stop(self):
        cip_settings.save_settings()

    def workspace_dir(self):
        self.initial_preparation()
        cip_settings.register_panels_to_update(self)
        # any move out CodeInPython mode, should logout user
        cip_settings.user_name = ""

        self.tabs_remove()
        self.workspace_update()

        # check, if in default place is present update.
        if cip_settings.zip_check_content():
            if cip_settings.show_comparision_of_zip_files():
                cip_settings.update_data_from_zip()
            else:
                os.remove(cip_settings.path_get("zip install default file"))

        if self.view.current_tab and self.view.current_tab.path:
            path = path_clean(self.view.current_tab.path)
        else:
            path = cip_settings.path_get("workspace")
        return path

    def api(self):
        """
        Return a list of API specifications to be used by auto-suggest and call
        tips.
        """
        return SHARED_APIS + PYTHON3_APIS + PI_APIS + PYGAMEZERO_APIS

    def play_toggle(self, event):
        """
        Handles the toggling of the play button to start/stop a script.
        """
        if self.runner:
            self.stop_game()
            play_slot = self.view.button_bar.slots["run"]
            play_slot.setIcon(load_icon("run"))
            play_slot.setText(_("Run"))
            play_slot.setToolTip(_("Run your CodeInPython program."))
            self.set_buttons(modes=True)
        else:
            self.run_game()
            if self.runner:
                play_slot = self.view.button_bar.slots["run"]
                play_slot.setIcon(load_icon("stop"))
                play_slot.setText(_("Stop"))
                play_slot.setToolTip(_("Stop your CodeInPython program."))
                self.set_buttons(modes=False)

    def run_game(self):
        """
        Run the current game.
        """
        # Grab the Python file.
        tab = self.view.current_tab
        if tab is None:
            logger.debug("There is no active text editor.")
            self.stop_game()
            return
        if tab.path is None:
            # Unsaved file.
            self.editor.save()
        if tab.path:
            # If needed, save the script.
            if tab.isModified():
                self.editor.save_tab_to_file(tab)
            logger.debug(tab.text())
            envars = self.editor.envars
            args = ["-m", "pgzero"]
            cwd = os.path.dirname(tab.path)

            self.runner = self.view.add_python3_runner(
                interpreter=venv.interpreter,
                script_name=tab.path,
                working_directory=cwd,
                interactive=False,
                envars=envars,
                python_args=args,
                command_args=None,
            )
            self.runner.process.waitForStarted()

    def stop_game(self):
        """
        Stop the currently running game.
        """
        logger.debug("Stopping script.")
        if self.runner:
            self.runner.stop_process()
            self.runner = None
        self.view.remove_python_runner()

    def initial_preparation(self):
        path_for_codeinpython_add()
        path_for_codeinpython_add(cip_settings.path_get("main"))
        self.register_path_for_cip_env()

        cip_settings.language = self.editor.user_locale

        # let's check if required folders are exists. If not, create it.
        for element in (
            "main",
            "workspace",
            "libraries",
            "examples",
            "custom examples",
            "firmware",
            "zip_archive",
            "mu_modules",
        ):
            check_path = cip_settings.path_get(element)
            if not os.path.isdir(check_path):
                os.makedirs(check_path)

        # temp. We want to add some modules into venv.
        # for now, it will be done in this way
        # (until I will not find better way)
        # later, this could be expand to possibility to install modules,
        # base on info in user project (description.xml)
        # In other word, module will be installed, when it really will be needed.
        _, user_packages = venv.installed_packages()
        old_packages = [p.lower() for p in user_packages]
        new_packages = ["pyserial"]
        if old_packages != new_packages:
            self.editor.sync_package_state(old_packages, new_packages)

    def register_path_for_cip_env(self):
        """
        Register path for CodeInPython environment in venv used by users.
        """
        # I'm not sure is that best way to do it. For now, is OK
        cip_main_path = cip_settings.path_get("main")
        file_path = os.path.join(
            get_python_lib(0, 0, venv.path), cip_settings.SEARCH_PATH_FILENAME
        )

        if not os.path.isfile(file_path):
            with open(file_path, "w") as config_path_file:
                config_path_file.write(cip_main_path)

    def update(self):
        """
        Update all info on screen, tabs, files, etc.
        """
        self.workspace_update()
        self.tabs_remove()

    def user_dialog(self, event):
        """
        Open dialog with student about lessons, that he want to use.
        """
        self.dialog_user = QtWidgets.QDialog()
        self.ui_user = Ui_Dialog_Users()
        self.ui_user.setupUi(self.dialog_user)

        cip_settings.subpath_class = cip_settings.get_setting("class_name")

        self.ui_user.user_edit.setText(cip_settings.user_name)
        self.ui_user.example_button.setEnabled(False)
        # to rid problems with special chars (i.e. when it will become
        # folder name), set of possible to use chars are limited.
        self.ui_user.user_edit.setValidator(
            QRegExpValidator(QRegExp(r"[0-9A-Za-z_+=-]{20}"))
        )
        self.ui_user.lessons_label.setText("...")
        self.ui_user.lesson_use_button.setEnabled(False)
        self.ui_user.lesson_remove_button.setEnabled(False)

        self.ui_user.examples_tree.itemClicked.connect(
            self.examples_tree_clicked
        )
        self.ui_user.example_button.clicked.connect(self.add_example_clicked)
        self.ui_user.user_button.clicked.connect(self.user_change_clicked)
        self.ui_user.user_edit.returnPressed.connect(self.user_change_clicked)
        self.ui_user.lessons_list.clicked.connect(self.lessons_list_clicked)
        self.ui_user.lessons_list.doubleClicked.connect(
            self.lesson_use_clicked
        )
        self.ui_user.lesson_use_button.clicked.connect(self.lesson_use_clicked)
        self.ui_user.lesson_remove_button.clicked.connect(
            self.lesson_remove_clicked
        )

        self.workspace_update()
        self.dialog_user.exec()

    def examples_tree_clicked(self, item, column):
        if item.directory != "" and cip_settings.user_name != "":
            self.ui_user.example_button.setEnabled(True)
        else:
            self.ui_user.example_button.setEnabled(False)

    def add_example_clicked(self):
        # check if added folder exist
        if self.ui_user.examples_tree.currentItem() is not None:
            dir_to_example = self.ui_user.examples_tree.currentItem().directory
            if dir_to_example == "" or not os.path.isdir(dir_to_example):
                QtWidgets.QMessageBox.warning(
                    None,
                    _("Error!"),
                    _("Can't find such example"),
                    QtWidgets.QMessageBox.Ok,
                )
                return
            path_at_student = os.path.relpath(
                dir_to_example, cip_settings.path_get("main")
            )
            path_at_student = cip_settings.path_get("student", path_at_student)
            if os.path.exists(path_at_student):
                question = QtWidgets.QMessageBox.question(
                    None,
                    _("Question"),
                    _("This lesson already exist. Overwrite it?"),
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                )
                if question == QtWidgets.QMessageBox.Yes:
                    shutil.rmtree(path_at_student)
                else:
                    return
            shutil.copytree(dir_to_example, path_at_student)

            # reload all files that are already open (for current student)
            subpath_to_student = cip_settings.path_get("student")
            list_of_lessons_paths = []
            current_tab_path = self.view.current_tab.path
            for tab in self.view.widgets:
                if (
                    tab.path is not None
                    and os.path.commonpath([tab.path, subpath_to_student])
                    == subpath_to_student
                ):
                    list_of_lessons_paths.append(tab.path)
                    tab_id = self.view.tabs.indexOf(tab)
                    self.view.tabs.removeTab(tab_id)

            current_tab = None
            for element in list_of_lessons_paths:
                self.editor.direct_load(element)
                if os.path.samefile(current_tab_path, element):
                    current_tab = self.view.current_tab

            # set focus at same tab as before reloading files
            if current_tab is not None:
                self.view.focus_tab(current_tab)
            self.workspace_update()

    def example_check_correctness(self, dir):
        locked = False
        start_file = None
        if os.path.isdir(dir):
            desc_file = os.path.join(dir, cip_settings.EXAMPLE_DESC_FILENAME)
            if os.path.exists(desc_file):
                with open(desc_file, "r") as desc_xml:
                    try:
                        tree = ET.parse(desc_xml)
                        root = tree.getroot()
                        start_file = xml_get_with_lang(
                            root, "start_file", cip_settings.language
                        )
                        locked_result = xml_get_with_lang(root, "locked")
                        if locked_result == "True":
                            locked = True
                    except Exception:
                        locked = True

            if start_file is None:
                start_file = os.path.split(dir)[1] + ".py"

            # only file in root of project is allowed
            start_file = os.path.split(start_file)[1]
            full_dir = os.path.join(dir, start_file)

            if locked or not os.path.exists(full_dir):
                return False, full_dir
            else:
                return True, full_dir

    def lessons_list_them(self, directory, list_item):
        """fill list with lessons added by user"""
        full_dir = cip_settings.path_get("student", directory)
        if os.path.isdir(full_dir):
            for element in os.listdir(full_dir):
                if os.path.isdir(os.path.join(full_dir, element)):
                    if element == cip_settings.SUBPATH_TRASH:
                        continue
                    tooltip = None
                    desc_file = os.path.join(
                        full_dir, element, cip_settings.EXAMPLE_DESC_FILENAME
                    )
                    if os.path.exists(desc_file):
                        with open(desc_file, "r") as desc_xml:
                            try:
                                tree = ET.parse(desc_xml)
                                root = tree.getroot()
                                title = xml_get_with_lang(
                                    root,
                                    "title",
                                    cip_settings.language,
                                    separator=" ",
                                )
                                if title is None:
                                    title = element
                                desc = xml_get_with_lang(
                                    root,
                                    "description",
                                    cip_settings.language,
                                    separator=" ",
                                )
                                if desc is None:
                                    desc = ".{}{}".format(
                                        os.path.sep,
                                        os.path.relpath(directory, element),
                                    )
                                tooltip = xml_get_with_lang(
                                    root,
                                    "tooltip",
                                    cip_settings.language,
                                    separator="\n",
                                )
                                whole_name = "{}\n  {}".format(title, desc)
                            except Exception:
                                whole_name = "!!!> {}\n  .{}{}".format(
                                    element,
                                    os.path.sep,
                                    os.path.join(directory, element),
                                )
                    else:
                        whole_name = "{}\n  .{}{}".format(
                            element,
                            os.path.sep,
                            os.path.join(directory, element),
                        )

                    result, *_ = self.example_check_correctness(
                        os.path.join(full_dir, element)
                    )
                    if result:
                        item = QtWidgets.QListWidgetItem(list_item)
                        item.directory = os.path.join(full_dir, element)
                        item.setText(whole_name)
                        item.setIcon(load_icon("package"))
                        if tooltip is not None:
                            item.setToolTip(tooltip)
                    self.lessons_list_them(
                        os.path.join(directory, element), list_item
                    )

    def lesson_remove_clicked(self):
        """remove chosen lesson"""
        list_item = self.ui_user.lessons_list.currentItem()
        if list_item is not None:
            if os.path.isdir(list_item.directory):
                question = QtWidgets.QMessageBox.question(
                    None,
                    _("Question"),
                    _("Are you sure, to remove these lesson?"),
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                )
                if question == QtWidgets.QMessageBox.Yes:
                    result, project_file = self.example_check_correctness(
                        list_item.directory
                    )

                    # make trash bin if not yet exist
                    trash_path = cip_settings.path_get(
                        "student",
                        cip_settings.SUBPATH_TRASH,
                        datetime.now().strftime("%Y_%m_%d %H-%M-%S"),
                    )
                    if not os.path.isdir(trash_path):
                        os.makedirs(trash_path)
                    shutil.move(list_item.directory, trash_path)
                    remove_empty_folders(
                        cip_settings.path_get("student"),
                        [cip_settings.EXAMPLE_DESC_FILENAME],
                    )
                    self.tabs_remove(project_file)
                    self.workspace_update()

    def lesson_custom_add(self):
        result, entered_name = Dialog_GetText(
            "input file name", r"[0-9A-Za-z_+=-]{20}"
        ).get()
        if result and entered_name != "":
            dir_name = os.path.splitext(entered_name)[0]
            file_name = dir_name + ".py"
            path_to_file = cip_settings.path_get(
                "student private", dir_name, file_name
            )

            if os.path.exists(path_to_file):
                question = QtWidgets.QMessageBox.question(
                    None,
                    _("Question"),
                    _("This lesson already exist. Overwrite it?"),
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                )
                if question == QtWidgets.QMessageBox.Yes:
                    shutil.rmtree(
                        cip_settings.path_get("student private", dir_name)
                    )
                else:
                    return False, ""

            try:
                # attempt to make following private lesson
                os.makedirs(cip_settings.path_get("student private", dir_name))
                with open(path_to_file, "w"):
                    pass

                # if this lesson is already open, reload it
                for tab in self.view.widgets:
                    if tab.path == path_to_file:
                        tab_id = self.view.tabs.indexOf(tab)
                        self.view.tabs.removeTab(tab_id)
                        self.editor.direct_load(path_to_file)
                        break
                return True, path_to_file

            except Exception as err:
                label_text = _("Error during creating file")
                label_text += ':\n{}: "{}"'.format(
                    type(err).__name__, str(err)
                )
                QtWidgets.QMessageBox.warning(
                    None,
                    _("Info"),
                    label_text,
                    QtWidgets.QMessageBox.Ok,
                )
                return False, ""

    def lessons_list_clicked(self):
        if hasattr(self, "dialog_user"):
            if self.ui_user.lessons_list.currentRow() >= 0:
                # in this way is recognized item dedicated to add private files
                if (
                    self.ui_user.lessons_list.currentItem().directory
                    == cip_settings.SUBPATH_PRIVATE_EXAMPLES
                ):
                    self.lesson_custom_add()
                    self.workspace_update()
                else:
                    self.ui_user.lesson_use_button.setEnabled(True)
                    self.ui_user.lesson_remove_button.setEnabled(True)
            else:
                self.ui_user.lesson_use_button.setEnabled(False)
                self.ui_user.lesson_remove_button.setEnabled(False)

    def lesson_use_clicked(self):
        """clicked button USE lesson
        or double clicked on lesson"""
        current_lesson_item = self.ui_user.lessons_list.currentItem()
        if (
            current_lesson_item is not None
            and current_lesson_item.directory != ""
        ):
            result, project_file = self.example_check_correctness(
                current_lesson_item.directory
            )
            if not result:
                QtWidgets.QMessageBox.warning(
                    None,
                    _("Info"),
                    _("Error during opening project"),
                    QtWidgets.QMessageBox.Ok,
                )
                self.workspace_update()
                self.tabs_remove()
                return
            for tab in self.view.widgets:
                if (
                    tab.path is not None
                    and tab.path == current_lesson_item.directory
                ):
                    self.view.focus_tab(tab)
                    self.dialog_user.close()
                    break
            else:  # if lesson isn't already open, then ...
                self.editor.direct_load(project_file)
                self.dialog_user.close()

    def tabs_remove(self, path_to_clean=""):
        """remove one tab or all tabs open by current user"""
        repaired_parh = path_clean(path_to_clean)
        if repaired_parh == "":
            # close all tabs with our students lessons
            subpath_to_workspace = cip_settings.path_get("workspace")
            for tab in self.view.widgets:
                if (
                    tab.path is not None
                    and os.path.commonpath([tab.path, subpath_to_workspace])
                    == subpath_to_workspace
                ):
                    tab_id = self.view.tabs.indexOf(tab)
                    self.view.tabs.removeTab(tab_id)
        else:
            # close one tab
            for tab in self.view.widgets:
                if tab.path is not None and tab.path == repaired_parh:
                    tab_id = self.view.tabs.indexOf(tab)
                    self.view.tabs.removeTab(tab_id)
                    break

    def workspace_update(self):
        """refresh main window with all data correspond to CodeInPython"""
        play_slot = self.view.button_bar.slots["user"]
        play_slot.setText(cip_settings.user_name)

        if cip_settings.user_name != "":
            play_slot.setIcon(load_icon("user"))
        else:
            play_slot.setIcon(load_icon("user_missing"))

        # if exist, refresh also data in user panel
        if hasattr(self, "dialog_user"):
            self.ui_user.user_edit.setText(cip_settings.user_name)
            if cip_settings.user_name != "":
                self.ui_user.lessons_label.setText(
                    _("These are your lessons, ") + cip_settings.user_name
                )
            else:
                self.ui_user.lessons_label.setText("...")

            self.lessons_fill_form()
            self.examples_fill_form()
            self.lessons_list_clicked()  # to update buttons status

    def user_change_clicked(self):
        if hasattr(self, "dialog_user"):
            if cip_settings.user_name != self.ui_user.user_edit.text():
                cip_settings.user_name = self.ui_user.user_edit.text()
                self.workspace_update()
                self.tabs_remove()

    def _examples_nodes_add(self, base, folder, parent_item):
        """Add examples base on structure of folders with examples
        Additional info could be taken from files description.xml
        (one at each folder)"""
        for element in os.listdir(os.path.join(base, folder)):
            full_dir = os.path.join(base, folder, element)
            locked = False
            if os.path.isdir(full_dir):
                item = QtWidgets.QTreeWidgetItem(parent_item)
                # first, we check if in this folder is present desc file
                desc_file = os.path.join(
                    full_dir, cip_settings.EXAMPLE_DESC_FILENAME
                )
                if os.path.exists(desc_file):
                    with open(desc_file, "r") as desc_xml:
                        try:
                            tree = ET.parse(desc_xml)
                            root = tree.getroot()
                            title = xml_get_with_lang(
                                root,
                                "title",
                                cip_settings.language,
                                separator=" ",
                            )
                            if title is None:
                                title = element
                            desc = xml_get_with_lang(
                                root,
                                "description",
                                cip_settings.language,
                                separator=" ",
                            )
                            if desc is None:
                                desc = ".{}{}".format(
                                    os.path.sep,
                                    os.path.relpath(full_dir, base),
                                )
                            tooltip = xml_get_with_lang(
                                root,
                                "tooltip",
                                cip_settings.language,
                                separator="\n",
                            )
                            if tooltip is not None:
                                item.setToolTip(0, tooltip)
                            locked_result = xml_get_with_lang(root, "locked")
                            if locked_result == "True":
                                locked = True
                            item.setText(0, "{}\n  {}".format(title, desc))
                        except Exception:
                            item.setText(
                                0,
                                "!!!> {}\n  .{}{}".format(
                                    element,
                                    os.path.sep,
                                    os.path.relpath(full_dir, base),
                                ),
                            )
                            locked = True
                else:
                    item.setText(
                        0,
                        "{}\n  .{}{}".format(
                            element,
                            os.path.sep,
                            os.path.relpath(full_dir, base),
                        ),
                    )
                    locked = not os.path.exists(
                        os.path.join(full_dir, element + ".py")
                    )

                if locked:
                    item.directory = ""
                    item.setIcon(0, load_icon("folder"))
                else:
                    item.directory = full_dir
                    item.setIcon(0, load_icon("package"))
                self._examples_nodes_add(
                    base, os.path.join(folder, element), item
                )

    def examples_fill_form(self):
        # first, clear list of all examples, then refill them
        self.ui_user.examples_tree.clear()
        self.ui_user.examples_tree.clearSelection()
        try:
            # CodeInPython examples
            self._examples_nodes_add(
                cip_settings.path_get("examples"),
                "",
                self.ui_user.examples_tree,
            )

            # custom examples
            item = QtWidgets.QTreeWidgetItem(self.ui_user.examples_tree)
            item.setText(0, _("Custom examples"))
            item.directory = ""
            item.setIcon(0, load_icon("folder"))
            self._examples_nodes_add(
                cip_settings.path_get("custom examples"), "", item
            )
        except Exception as err:
            label_text = _("Error during building examples structure")
            label_text += ':\n{}: "{}"'.format(type(err).__name__, str(err))
            QtWidgets.QMessageBox.warning(
                None, _("Info"), label_text, QtWidgets.QMessageBox.Ok
            )

    def lessons_fill_form(self):
        self.ui_user.lessons_list.clear()
        self.ui_user.lessons_list.clearSelection()
        if cip_settings.user_name != "":
            self.lessons_list_them("", self.ui_user.lessons_list)
            # new private program
            item = QtWidgets.QListWidgetItem(self.ui_user.lessons_list)
            item.directory = cip_settings.SUBPATH_PRIVATE_EXAMPLES
            item.setText("Add new own program")
            item.setIcon(load_icon("new"))

    #  DEVICES tab (for users)
    def devices_dialog(self):
        """Open basic form (framework) for CodeInPython devices.
        Later on, is called class from external module. In this way,
        we can easy make updates for this device window, without
        necessity update Mu Editor"""
        try:
            cip_dev_lib = importlib.import_module(
                "{}.{}".format(
                    cip_settings.SUBPATH_MU_MODULE,
                    cip_settings.DEVICES_MODULE_NAME,
                )
            )
            cip_dev_lib.Device_User_Panel(self, cip_settings.language)

        except Exception as err:
            label_text = _("Loading CodeInPython library failed")
            label_text += '\n  {}: "{}"'.format(type(err).__name__, str(err))
            label_text += "\nHave you loaded add-ons from CodeInPython?"
            QtWidgets.QMessageBox.warning(
                None, _("Info"), label_text, QtWidgets.QMessageBox.Ok
            )


class CodeInPython_Config_Tab(QtWidgets.QWidget):
    """This is tab dedicated to configure CodeInPython environment"""

    # version_on_server = "??"
    # firmware_port = None
    # firmware_device_name = ""
    # firmware_installed_version = ""
    # settings = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        cip_settings.register_panels_to_update(self)

    def setup(self, settings):
        self.settings = settings
        self.ui_config_tab = Ui_Tab_Settings()
        self.ui_config_tab.setupUi(self.parent)
        self.parent.setMinimumSize(600, 430)
        self.parent.setWindowTitle(_("Mu Administration"))
        self.setLayout(self.ui_config_tab.verticalLayout)
        self.ui_config_tab.settings_class_edit.setValidator(
            QRegExpValidator(QRegExp(r"[0-9A-Za-z_+=-]{20}"))
        )

        self.ui_config_tab.settings_class_edit.returnPressed.connect(
            self.settings_class_click
        )
        self.ui_config_tab.update_server_button.clicked.connect(
            self.update_server_button_click
        )
        self.ui_config_tab.update_zip_button.clicked.connect(
            self.update_zip_button_click
        )
        self.ui_config_tab.service_button.clicked.connect(
            self.service_button_click
        )
        self.parent.accepted.connect(self.tab_accepted_click)
        self.ui_config_tab.settings_class_adv_button.clicked.connect(
            self.settings_class_adv_click
        )
        self.update()

    def update(self):
        # update all data on this tab
        if hasattr(self, "ui_config_tab"):
            # for now, validation is base on checking only zip from archive
            # Maybe better will be, make large method with checking all folders,
            # content inside, etc. To determinate later...
            valid, version_info = cip_settings.zip_get_version(
                cip_settings.path_get("zip archive file")
            )
            if valid:
                desc = (
                    _("Version of the currently installed software:")
                    + version_info["release"]
                )
                desc += "\nDate: " + version_info["date"]
            else:
                desc = _(
                    "Error. "
                    "There is no installed proper software from CodeInPython"
                )
            self.ui_config_tab.update_label.setText(desc)

            name = cip_settings.get_setting("class_name")
            self.ui_config_tab.settings_class_edit.setText(name)

    def settings_save_data(self):
        # save data, clear user, refresh all panels
        if hasattr(self, "ui_config_tab"):
            student_class = self.ui_config_tab.settings_class_edit.text()
            cip_settings.put_setting("class_name", student_class)
            cip_settings.user_name = ""
            cip_settings.update_all_panels_now()

    def settings_class_click(self):
        self.settings_save_data()

    def settings_class_adv_click(self):
        # This is just for start (now not used). We can put here additional
        # adv settings like: network path.
        modifiers = QtWidgets.QApplication.keyboardModifiers()
        # It will work only with CTRL + ALT keys
        if modifiers == QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier:
            result, path = Dialog_GetText(
                _("Input network path to system configuration file"),
                r"[0-9A-Za-z_+=-.:/\\]{300}",
            ).get()
            if result:
                # TODO
                # check if at this path is present config file.
                # This will be master config,
                # overwritting all local configuration
                print("Addres for config file is:", path)

    def tab_accepted_click(self):
        self.settings_save_data()

    def service_button_click(self):
        try:
            cip_dev_lib = importlib.import_module(
                "{}.{}".format(
                    cip_settings.SUBPATH_MU_MODULE,
                    cip_settings.DEVICES_MODULE_NAME,
                )
            )
            cip_dev_lib.Device_Service_Panel(
                self, cip_settings.language, cip_settings.path_get("firmware")
            )

        except Exception as err:
            label_text = _("Loading CodeInPython library failed")
            label_text += ':\n{}: "{}"'.format(type(err).__name__, str(err))
            label_text += "\nHave you loaded add-ons from CodeInPython?"
            QtWidgets.QMessageBox.warning(
                None, _("Info"), label_text, QtWidgets.QMessageBox.Ok
            )

    def update_server_button_click(self):
        """Get update from www"""
        url_zip = urllib.parse.urljoin(
            cip_settings.URL_FOR_UPDATE_FILE, cip_settings.ZIP_FILENAME
        )
        # prepare temp folder to download there zip file
        temp_path = cip_settings.path_get("main", "temp_internet")
        shutil.rmtree(temp_path, ignore_errors=True)
        os.makedirs(temp_path)

        output_file = os.path.join(temp_path, cip_settings.ZIP_FILENAME)
        try:
            with urllib.request.urlopen(url_zip) as response:
                with open(output_file, "wb") as out_file:
                    shutil.copyfileobj(response, out_file)
            if cip_settings.show_comparision_of_zip_files(output_file):
                cip_settings.update_data_from_zip(output_file)
        except OSError:
            label_text = _(
                "Cannot download proper software.\n"
                "Please check internet connection"
            )
            QtWidgets.QMessageBox.warning(
                None, _("Info"), label_text, QtWidgets.QMessageBox.Ok
            )
            return
        finally:
            shutil.rmtree(temp_path, ignore_errors=True)
            self.update()

    def update_zip_button_click(self):
        """Get update from local zip file"""
        filename = QtWidgets.QFileDialog.getOpenFileName(
            self,
            _("Select zip file to copy (.zip)"),
            cip_settings.path_get("main"),
            _("Zip compressed file (*.zip)"),
        )
        if filename and filename[0] != "":
            if cip_settings.show_comparision_of_zip_files(filename[0]):
                cip_settings.update_data_from_zip(filename[0])
        self.update()
