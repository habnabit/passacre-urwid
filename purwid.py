import collections
import json
import os

from passacre.application import Passacre, is_likely_hashed_site
from twisted.internet import defer
import urwid

import pencrypt


class Application(object):
    def __init__(self):
        self._popup_stack = []

    def start(self):
        self.passacre = Passacre()
        all_sites = {k: v
                     for k, v in self.passacre.config.get_all_sites().iteritems()
                     if not is_likely_hashed_site(k)}
        all_schemata = self.passacre.config.get_all_schemata()

        headings = [
            ('Sites', SiteList(all_sites)),
            ('Schemata', FilteringChoiceBox({k: PassacreSchemaRow(k, v) for k, v in all_schemata.iteritems()})),
        ]
        self.widget = urwid.WidgetPlaceholder(Headings(headings))
        return self.widget

    def popup(self, widget, **kw):
        kw.setdefault('align', 'center')
        kw.setdefault('valign', 'middle')
        self._popup_stack.append(self.widget.original_widget)
        self.widget.original_widget = urwid.Overlay(
            widget, self.widget.original_widget, **kw)

    def close_popup(self):
        self.widget.original_widget = self._popup_stack.pop()

    def prompt_password(self, prompt):
        dialog = PasswordPromptDialog(prompt)
        self.popup(dialog, width='pack', height='pack')
        return dialog.deferred


app = Application()


class SelectableText(urwid.WidgetWrap):
    def __init__(self, *a, **kw):
        self._w = urwid.Text(*a, **kw)

    def selectable(self):
        return True

    def keypress(self, size, key):
        return key


class Headings(urwid.WidgetWrap):
    def __init__(self, headings):
        self.headings = headings
        self.heading_widget = urwid.Columns([
            urwid.AttrMap(SelectableText(h, align='center'),
                          None, focus_map='reversed')
            for h, _ in self.headings
        ])
        self.page_display = urwid.WidgetPlaceholder(self.headings[0][1])
        self._w = urwid.Pile([
            ('pack', self.heading_widget),
            self.page_display,
        ])

    def render(self, size, focus=False):
        self.page_display.original_widget = (
            self.headings[self.heading_widget.focus_position][1])
        return self.__super.render(size, focus)


class FixedAdapter(urwid.WidgetWrap):
    def __init__(self, widget, size):
        self.__super.__init__(widget)
        self._size = size

    def sizing(self):
        return frozenset(['fixed'])

    def pack(self, size, focus=False):
        return self._size

    def render(self, size, focus=False):
        return self._w.render(self._size, focus)

    def keypress(self, size, key):
        return self._w.keypress(self._size, key)


def dialog(widget, title='', size=None):
    w = urwid.LineBox(
        urwid.Filler(
            urwid.Padding(widget, left=2, right=2),
            top=1, bottom=1, height=('relative', 100)),
        title=title)
    if size is not None:
        w = FixedAdapter(w, size)
    return w


class PasswordPromptDialog(urwid.WidgetWrap):
    app = app

    def __init__(self, prompt):
        self._edit = urwid.Edit(mask='*')
        self._w = dialog(urwid.ListBox(urwid.SimpleListWalker([
            urwid.Text(prompt),
            urwid.Divider(),
            self._edit,
        ])), size=(40, 7))
        self.deferred = defer.Deferred()

    def keypress(self, size, key):
        if key == 'enter':
            self.app.close_popup()
            self.deferred.callback(self._edit.edit_text)
        return self._w.keypress(size, key)


class ConfigRow(urwid.WidgetWrap):
    app = app

    def __init__(self, name, value):
        self.name = name
        self.value = value
        self._w = urwid.Columns([
            urwid.Text(self.name + ': '),
            ('weight', 2, urwid.Text(str(self.value))),
        ])


class SiteInfoDialog(urwid.WidgetWrap):
    app = app

    def __init__(self, site_row):
        self.site_row = site_row

        self._method_group = []
        method = site_row.config['method']
        self._yubikey_group = []
        yubikey_slot = site_row.config.get('yubikey-slot')

        self._config_rows = urwid.SimpleListWalker([
            urwid.Edit('Schema: ', str(site_row.config['schema'])),
            urwid.Edit('Username: ', site_row.config.get('username', '')),
            urwid.IntEdit('Increment: ', site_row.config.get('increment', 0)),
            urwid.Divider(),
            urwid.Text('Hash method:'),
            urwid.RadioButton(self._method_group, 'Keccak', method == 'keccak'),
            urwid.RadioButton(self._method_group, 'Skein', method == 'skein'),
            urwid.Divider(),
            urwid.Text('YubiKey challenge/response slot:'),
            urwid.RadioButton(self._yubikey_group, 'no YubiKey', yubikey_slot is None),
            urwid.RadioButton(self._yubikey_group, 'slot 1', yubikey_slot == 1),
            urwid.RadioButton(self._yubikey_group, 'slot 2', yubikey_slot == 2),
        ])
        close_button = urwid.Button('OK')
        urwid.connect_signal(close_button, 'click', lambda x: self.app.close_popup())
        pile = urwid.Pile([
            urwid.ListBox(self._config_rows),
            ('pack', close_button),
        ])
        pile.focus_position = 1
        self._w = dialog(pile, title=site_row.site_name)

    def keypress(self, size, key):
        if key == 'g':
            self.app.popup(PasswordPromptDialog('hi'), width='pack', height='pack')
            return
        return self._w.keypress(size, key)


class PassacreSiteRow(urwid.WidgetWrap):
    app = app

    def __init__(self, site_name, config):
        self.site_name = site_name
        self.config = config
        self._w = urwid.AttrMap(
            urwid.Padding(SelectableText(site_name)),
            None, focus_map='reversed')

    def keypress(self, size, key):
        if key == 'enter':
            self.app.popup(SiteInfoDialog(self), width=('relative', 50), height=('relative', 50))
        return key


class SchemaInfoDialog(urwid.WidgetWrap):
    app = app

    def __init__(self, schema_row):
        self.schema_row = schema_row
        close_button = urwid.Button('OK')
        urwid.connect_signal(close_button, 'click', lambda x: self.app.close_popup())
        pile = urwid.Pile([
            ('pack', urwid.Text(str(schema_row.schema))),
            urwid.SolidFill(),
            ('pack', close_button),
        ])
        pile.focus_position = 2
        self._w = dialog(pile, title=schema_row.schema_name)



class PassacreSchemaRow(urwid.WidgetWrap):
    app = app

    def __init__(self, schema_name, schema):
        self.schema_name = schema_name
        self.schema = schema
        self._w = urwid.AttrMap(
            urwid.Padding(SelectableText(schema_name)),
            None, focus_map='reversed')

    def keypress(self, size, key):
        if key == 'enter':
            self.app.popup(SchemaInfoDialog(self), width=('relative', 50), height=('relative', 50))
        return key


def merge_sorted_lists(src, dst):
    if not dst:
        dst[:] = src
        return
    direction = cmp(len(src), len(dst))
    if direction == 1:
        pos = len(dst)
        for x in reversed(src):
            if dst[pos - 1] != x:
                dst.insert(pos, x)
            elif pos > 0:
                pos -= 1
    elif direction == -1:
        to_keep = set(src)
        for e, x in reversed(list(enumerate(dst))):
            if x not in to_keep:
                del dst[e]


class FilteringChoiceBox(urwid.WidgetWrap):
    def __init__(self, widget_map):
        self._all_widgets = dict(widget_map)
        self._widget_list = urwid.SimpleFocusListWalker([])
        self._widget_list_box = urwid.ListBox(self._widget_list)
        self._filter = ''
        self._filter_text = urwid.Text('')
        self._w = urwid.Pile([self._widget_list_box, ('pack', self._filter_text)])
        self.filtering = False
        self._filter_choices()
        if self._widget_list:
            self._widget_list_box.set_focus(0)

    def _filtered_choices(self):
        counts = collections.Counter(self._filter)
        for label, widget in self._all_widgets.iteritems():
            widget_counts = collections.Counter(label)
            if counts & widget_counts == counts:
                yield label, widget

    def _filter_choices(self):
        to_keep = [w for _, w in sorted(self._filtered_choices())]
        merge_sorted_lists(to_keep, self._widget_list)

    def add_widgets(self, widgets):
        self._all_widgets.update(widgets)
        self._filter_choices()

    def keypress(self, size, key):
        if not self.filtering:
            if key == '/':
                self.filtering = True
                return
            return self.__super.keypress(size, key)
        if len(key) == 1:
            self._filter += key
        elif key == 'backspace':
            self._filter = self._filter[:-1]
        elif key == 'esc':
            self._filter = ''
            self.filtering = False
        else:
            return self._widget_list_box.keypress(size, key)
        self._filter_text.set_text(self._filter)
        self._filter_choices()


class SiteList(urwid.WidgetWrap):
    app = app

    def __init__(self, all_sites):
        self.all_sites = all_sites
        self._filter = FilteringChoiceBox({k: PassacreSiteRow(k, v) for k, v in all_sites.iteritems()})
        self._w = urwid.Pile([
            self._filter,
            ('pack', urwid.Text(' <F1> Load encrypted sites  <F2> Save encrypted sites')),
        ])

    def keypress(self, size, key):
        if key == 'f1':
            d = self.app.prompt_password('Password for decryption:')
            d.addCallback(self._file_of_password)
            d.addCallback(self._decrypt_sites)
        elif key == 'f2':
            d = self.app.prompt_password('Password for encryption:')
            d.addCallback(self._file_of_password)
            d.addCallback(self._encrypt_sites)
        else:
            return self.__super.keypress(size, key)

    def _file_of_password(self, password):
        box = pencrypt.box_of_config_and_password(self.app.passacre.config, password)
        fobj = pencrypt.EncryptedFile(box, os.path.expanduser('~/.config/passacre/sites'))
        return fobj

    def _decrypt_sites(self, fobj):
        data = json.loads(fobj.read().decode())
        d = self.app.prompt_password('Password for hashed sites:')
        d.addCallback(self._add_hashed_sites, data)

    def _add_hashed_sites(self, password, sites):
        site_data = {site: self.app.passacre.config.get_site(site, password)
                     for site in sites}
        self.all_sites.update(site_data)
        self._filter.add_widgets({k: PassacreSiteRow(k, v)
                                  for k, v in site_data.iteritems()})

    def _encrypt_sites(self, fobj):
        data = json.dumps(list(self.all_sites)).encode()
        fobj.write(data)


if __name__ == '__main__':
    urwid.MainLoop(
        app.start(),
        palette=[('reversed', 'black', 'light gray')]).run()
