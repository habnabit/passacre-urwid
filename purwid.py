import collections

from passacre.agent import commands
from passacre.application import Passacre, is_likely_hashed_site
from twisted.internet import endpoints, task, defer
from twisted.protocols.amp import AMP
import urwid


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
        self.mainloop = urwid.MainLoop(
            self.widget,
            palette=[('reversed', 'black', 'light gray')],
            event_loop=urwid.TwistedEventLoop())
        return self.mainloop

    def popup(self, widget, **kw):
        kw.setdefault('align', 'center')
        kw.setdefault('valign', 'middle')
        self._popup_stack.append(self.widget.original_widget)
        self.widget.original_widget = urwid.Overlay(
            widget, self.widget.original_widget, **kw)
        self.mainloop.draw_screen()

    def close_popup(self):
        self.widget.original_widget = self._popup_stack.pop()
        self.mainloop.draw_screen()

    def prompt_password(self, prompt):
        dialog = PasswordPromptDialog(prompt)
        self.popup(dialog, width='pack', height='pack')
        return dialog.deferred

    def wrap_deferred(self, deferred, caption, with_errback=True):
        dialog = DeferredDisplayDialog(deferred, caption)
        self.popup(dialog, width='pack', height='pack')
        if with_errback:
            deferred.addErrback(self.show_errback)
        return deferred

    def show_errback(self, failure, propagate=True):
        text = failure.getTraceback()
        self.display_text(text)
        if propagate:
            return failure

    def display_text(self, caption):
        dialog = TextDisplayDialog(caption)
        self.popup(dialog, width='pack', height='pack')


app = Application()


def run_amp_command(description, command, args, reactor=None):
    if reactor is None:
        from twisted.internet import reactor
    endpoint = endpoints.clientFromString(reactor, description)
    amp = AMP()
    d = endpoints.connectProtocol(endpoint, amp)
    d.addCallback(lambda ign: amp.callRemote(command, **args))
    return d


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
        elif key == 'esc':
            self.app.close_popup()
            self.deferred.errback(defer.CancelledError())
        return self._w.keypress(size, key)


class DeferredDisplayDialog(urwid.WidgetWrap):
    app = app

    def __init__(self, deferred, caption):
        self._cancel = urwid.Button('Cancel')
        self._w = dialog(urwid.ListBox(urwid.SimpleListWalker([
            urwid.Text(caption),
            urwid.Divider(),
            self._cancel,
        ])), size=(40, 7))
        deferred.addBoth(self._close)
        urwid.connect_signal(self._cancel, 'click', lambda x: deferred.cancel())

    def _close(self, result):
        self.app.close_popup()
        return result


class TextDisplayDialog(urwid.WidgetWrap):
    app = app

    def __init__(self, caption):
        self._ok = urwid.Button('OK')
        caption = urwid.Text(caption)
        w, h = caption.pack((116,))
        self._w = dialog(urwid.ListBox(urwid.SimpleListWalker([
            caption,
            urwid.Divider(),
            self._ok,
        ])), size=(w + 6, h + 6))
        urwid.connect_signal(self._ok, 'click', lambda x: self.app.close_popup())


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
            ('pack', urwid.Text(' <F1> Load site list')),
        ])

    def keypress(self, size, key):
        if key == 'f1':
            d = run_amp_command('tcp:localhost:9000', commands.FetchSiteList, {})
            self.app.wrap_deferred(d, 'Fetching site list...', with_errback=False)
            d.addCallback(self._add_sites)
            d.addErrback(self.app.show_errback, propagate=False)
        else:
            return self.__super.keypress(size, key)

    def _add_sites(self, results):
        sites = results['sites']
        d = self.app.prompt_password('Password for hashed sites:')
        d.addCallback(self._add_hashed_sites, sites)
        return d

    def _add_hashed_sites(self, password, sites):
        site_data = {site: self.app.passacre.config.get_site(site, password)
                     for site in sites
                     if site not in self.all_sites}
        self.all_sites.update(site_data)
        self._filter.add_widgets({k: PassacreSiteRow(k, v)
                                  for k, v in site_data.iteritems()})


if __name__ == '__main__':
    app.start().run()
