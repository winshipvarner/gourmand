import time
from gettext import ngettext
from typing import List, Tuple, Union

from gi.repository import Gdk, GdkPixbuf, GLib, GObject, Gtk, Pango

from gourmand.i18n import _

from . import Undo, convert
from .backends.db import RecipeManager
from .gdebug import debug
from .gglobals import DEFAULT_HIDDEN_COLUMNS, INT_REC_ATTRS, REC_ATTRS
from .gtk_extras import WidgetSaver, mnemonic_manager, pageable_store, ratingWidget
from .gtk_extras import cb_extras as cb
from .gtk_extras import treeview_extras as te
from .image_utils import bytes_to_pixbuf
from .importers.clipboard_importer import import_from_drag_and_drop
from .prefs import Prefs


class RecIndex:
    """We handle the 'index view' of recipes, which puts
    a recipe model into a tree and allows it to be searched
    and sorted. We're a separate class from the main recipe
    program so that we can be created again (e.g. in the recSelector
    dialog called from a recipe card."""

    default_searches = [{"column": "deleted", "operator": "=", "search": False}]

    def __init__(self, ui: Gtk.Builder, rd: RecipeManager, rg, editable: bool = False):
        self.editable = editable
        self.selected = True
        self.rtcols = rg.rtcols
        self.rtcolsdic = rg.rtcolsdic
        self.rtwidgdic = rg.rtwidgdic
        self.prefs = Prefs.instance()
        self.ui = ui
        self.rd = rd
        self.rg = rg  # RecGui
        self.searchByDic = {
            str(_("anywhere")): "anywhere",
            str(_("title")): "title",
            str(_("ingredient")): "ingredient",
            str(_("instructions")): "instructions",
            str(_("notes")): "modifications",
            str(_("category")): "category",
            str(_("cuisine")): "cuisine",
            str(_("source")): "source",
        }
        self.searchByList = [
            _("anywhere"),
            _("title"),
            _("ingredient"),
            _("category"),
            _("cuisine"),
            _("source"),
            _("instructions"),
            _("notes"),
        ]
        self.setup_widgets()

    def setup_widgets(self):
        self.srchentry = self.ui.get_object("rlistSearchbox")
        self.limitButton = self.ui.get_object("rlAddButton")
        self.SEARCH_MENU_KEY = "b"
        self.srchLimitBar = self.ui.get_object("srchLimitBar")
        assert self.srchLimitBar
        self.srchLimitBar.hide()
        self.srchLimitLabel = self.ui.get_object("srchLimitLabel")
        self.srchLimitClearButton = self.ui.get_object("srchLimitClear")
        self.srchLimitText = self.srchLimitLabel.get_text()
        self.srchLimitDefaultText = self.srchLimitText
        self.searchButton = self.ui.get_object("searchButton")
        self.rSearchByMenu = self.ui.get_object("rlistSearchByMenu")

        cb.set_model_from_list(self.rSearchByMenu, self.searchByList, expand=False)
        cb.setup_typeahead(self.rSearchByMenu)

        self.rSearchByMenu.set_active(0)
        self.rSearchByMenu.connect("changed", self.search_as_you_type)
        self.searchOptionsBox = self.ui.get_object("searchOptionsBox")

        self.search_options_tbtn = self.ui.get_object("searchOptionsToggle")
        self.search_options_tbtn.connect("toggled", self.search_options_toggle_callback)
        self.search_regex_tbtn = self.ui.get_object("regexpTog")
        self.search_regex_tbtn.connect("toggled", self.search_regex_toggle_callback)
        self.search_typing_tbtn = self.ui.get_object("searchAsYouTypeToggle")
        self.search_typing_tbtn.connect("toggled", self.search_typing_toggle_callback)

        self.search_actions = Gtk.ActionGroup(name="SearchActions")
        self.search_actions.add_toggle_actions(
            [
                (
                    "search_regex_toggle",
                    None,
                    _("Use regular expressions in search"),
                    None,
                    _("Use regular expressions (an advanced search language) in text search"),
                    self.search_regex_toggle_callback,
                    False,
                ),
                (
                    "search_typing_toggle",
                    None,
                    _("Search as you type"),
                    None,
                    _("Search as you type (turn off if search is too slow)."),
                    self.search_typing_toggle_callback,
                    True,
                ),
                ("search_options_toggle", None, _("Show Search _Options"), None, _("Show advanced searching options"), self.search_options_toggle_callback),
            ]
        )

        action = self.search_actions.get_action("search_regex_toggle")
        action.do_connect_proxy(action, self.search_regex_tbtn)
        self.search_regex_action = action

        action = self.search_actions.get_action("search_typing_toggle")
        action.do_connect_proxy(action, self.search_typing_tbtn)
        self.search_typing_action = action

        action = self.search_actions.get_action("search_options_toggle")
        action.do_connect_proxy(action, self.search_options_tbtn)

        self.rectree = self.ui.get_object("recTree")
        self.sw = self.ui.get_object("scrolledwindow")
        self.rectree.connect("start-interactive-search", lambda *args: self.srchentry.grab_focus())
        self.prev_button = self.ui.get_object("prevButton")
        self.next_button = self.ui.get_object("nextButton")
        self.first_button = self.ui.get_object("firstButton")
        self.last_button = self.ui.get_object("lastButton")
        self.showing_label = self.ui.get_object("showingLabel")
        self.stat = self.ui.get_object("statusbar")
        self.contid = self.stat.get_context_id("main")
        self.setup_search_views()
        self.setup_rectree()

        self.prev_button.connect("clicked", lambda *args: self.rmodel.prev_page())
        self.next_button.connect("clicked", lambda *args: self.rmodel.next_page())
        self.first_button.connect("clicked", lambda *args: self.rmodel.goto_first_page())
        self.last_button.connect("clicked", lambda *args: self.rmodel.goto_last_page())

        self.ui.connect_signals(
            {
                "rlistSearch": self.search_as_you_type,
                "ingredientSearch": lambda *args: self.set_search_by("ingredient"),
                "titleSearch": lambda *args: self.set_search_by("title"),
                "ratingSearch": lambda *args: self.set_search_by("rating"),
                "categorySearch": lambda *args: self.set_search_by("category"),
                "cuisineSearch": lambda *args: self.set_search_by("cuisine"),
                "search": self.search,
                "searchBoxActivatedCB": self.search_entry_activate_cb,
                "rlistReset": self.reset_search,
                "rlistLimit": self.limit_search,
                "search_as_you_type_toggle": self.search_typing_toggle_callback,
            }
        )

        # Set up widget saver for status across sessions
        self.rg.conf.append(
            WidgetSaver.WidgetSaver(self.search_typing_tbtn, self.prefs.get("sautTog", {"active": self.search_typing_tbtn.get_active()}), ["toggled"])
        )

        self.rg.conf.append(
            WidgetSaver.WidgetSaver(
                self.search_typing_action, self.prefs.get("sautTog", {"active": self.search_typing_action.get_active()}), ["toggled"], show=False
            )
        )

        self.rg.conf.append(
            WidgetSaver.WidgetSaver(self.search_regex_tbtn, self.prefs.get("regexpTog", {"active": self.search_regex_tbtn.get_active()}), ["toggled"])
        )
        self.rg.conf.append(
            WidgetSaver.WidgetSaver(
                self.search_regex_action, self.prefs.get("regexpTog", {"active": self.search_regex_action.get_active()}), ["toggled"], show=False
            )
        )
        # and we update our count with each deletion.
        self.rd.delete_hooks.append(self.set_reccount)
        # setup a history
        self.uim = self.ui.get_object("undo_menu_item")
        self.rim = self.ui.get_object("redo_menu_item")
        self.raim = self.ui.get_object("reapply_menu_item")
        self.history = Undo.UndoHistoryList(self.uim, self.rim, self.raim)
        # Fix up our mnemonics with some heavenly magic
        self.mm = mnemonic_manager.MnemonicManager()
        self.mm.sacred_cows.append("search for")  # Don't touch _Search for:
        self.mm.add_builder(self.ui)
        self.mm.add_treeview(self.rectree)
        self.mm.fix_conflicts_peacefully()

    def setup_search_views(self):
        """Setup our views of the database."""
        self.last_search = {}
        # self.rvw = self.rd.fetch_all(self.rd.recipe_table,deleted=False)
        self.searches = self.default_searches[0:]
        # List of entries in the `recipe` database table
        self.rvw = self.rd.search_recipes(self.searches, sort_by=self.sort_by)  # List["RowProxy"]

    def search_entry_activate_cb(self, *args):
        if self.rmodel._get_length_() == 1:
            self.rec_tree_select_rec()
        elif self.srchentry.get_text():
            if not self.search_as_you_type:
                self.search()
                GLib.idle_add(lambda *args: self.limit_search())
            else:
                self.limit_search()

    def rmodel_page_changed_cb(self, rmodel):
        if rmodel.page == 0:
            self.prev_button.set_sensitive(False)
            self.first_button.set_sensitive(False)
        else:
            self.prev_button.set_sensitive(True)
            self.first_button.set_sensitive(True)
        if rmodel.get_last_page() == rmodel.page:
            self.next_button.set_sensitive(False)
            self.last_button.set_sensitive(False)
        else:
            self.next_button.set_sensitive(True)
            self.last_button.set_sensitive(True)
        self.set_reccount()

    def rmodel_sort_cb(self, rmodel: "RecipeModel", sorts: List[Tuple[str, int]]):
        self.sort_by = sorts
        self.last_search = {}
        self.search()

    def setup_rectree(self):
        """Create our recipe treemodel."""
        recipes_per_page = self.prefs.get("recipes_per_page", 12)
        self.rmodel = RecipeModel(self.rvw, self.rd, per_page=recipes_per_page)

        self.rmodel.connect("page-changed", self.rmodel_page_changed_cb)
        self.rmodel.connect("view-changed", self.rmodel_page_changed_cb)
        self.rmodel.connect("view-sort", self.rmodel_sort_cb)
        # and call our handler once to update our prev/next buttons + label
        self.rmodel_page_changed_cb(self.rmodel)
        # and hook up our model
        self.rectree.set_model(self.rmodel)
        self.rectree.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        self.selection_changed()
        self.setup_reccolumns()
        # this has to come after columns are added or else adding columns resets out column order!
        self.rectree_conf = te.TreeViewConf(
            self.rectree, hidden=self.prefs.get("rectree_hidden_columns", DEFAULT_HIDDEN_COLUMNS), order=self.prefs.get("rectree_column_order", {})
        )
        self.rectree_conf.apply_column_order()
        self.rectree_conf.apply_visibility()
        self.rectree.connect("row-activated", self.rec_tree_select_rec)
        self.rectree.connect("key-press-event", self.tree_keypress_cb)
        self.rectree.get_selection().connect("changed", self.selection_changedCB)
        self.rectree.set_property("rules-hint", True)  # stripes!
        self.rectree.expand_all()
        self.rectree.show()

        self.rectree.enable_model_drag_dest([("text/plain", 0, 0)], Gdk.DragAction.COPY)
        self.rectree.connect("drag-data-received", import_from_drag_and_drop)

    def set_reccount(self, *args):
        """Display the count of currently visible recipes."""
        debug("set_reccount (self, *args):", 5)
        self.count = self.rmodel._get_length_()
        bottom, top, total = self.rmodel.showing()
        if top >= total and bottom == 1:
            lab = ngettext("%s recipe", "%s recipes", top) % top
            for b in self.first_button, self.prev_button, self.next_button, self.last_button:
                b.hide()
        else:
            for b in self.first_button, self.prev_button, self.next_button, self.last_button:
                b.show()
            # Do not translate bottom, top and total -- I use these fancy formatting
            # strings in case your language needs the order changed!
            lab = _("Showing recipes %(bottom)s to %(top)s of %(total)s") % locals()
        self.showing_label.set_markup("<i>" + lab + "</i>")
        if self.count == 1:
            sel = self.rectree.get_selection()
            if sel:
                sel.select_path((0,))

    def setup_reccolumns(self):
        """Setup the columns of our recipe index TreeView"""
        renderer = Gtk.CellRendererPixbuf()
        cssu = pageable_store.ColumnSortSetterUpper(self.rmodel)
        col = Gtk.TreeViewColumn("", renderer, pixbuf=1)
        col.set_min_width(-1)
        self.rectree.append_column(col)
        n = 2
        _title_to_num_ = {}
        for c in self.rtcols:
            if c == "rating":
                # special case -- for ratings we set up our lovely
                # star widget
                twsm = ratingWidget.TreeWithStarMaker(
                    self.rectree, self.rg.star_generator, data_col=n, col_title="_%s" % self.rtcolsdic[c], properties={"reorderable": True, "resizable": True}
                )
                cssu.set_sort_column_id(twsm.col, twsm.data_col)
                n += 1
                twsm.col.set_min_width(110)
                continue
            # And we also special case our time column
            elif c in ["preptime", "cooktime"]:
                _title_to_num_[self.rtcolsdic[c]] = n
                renderer = Gtk.CellRendererText()
                renderer.set_property("editable", True)
                renderer.connect("edited", self.rtree_time_edited_cb, n, c)

                def get_colnum(tc):
                    try:
                        t = tc.get_title()
                        if t:
                            return _title_to_num_[t.replace("_", "")]
                        else:
                            print("wtf, no title for ", tc)
                            return -1
                    except:
                        print("problem with ", tc)
                        raise

                ncols = self.rectree.insert_column_with_data_func(
                    -1,
                    "_%s" % self.rtcolsdic[c],
                    renderer,
                    lambda tc, cell, mod, titr: cell.set_property(
                        "text",
                        convert.seconds_to_timestring(
                            mod.get_value(
                                titr,
                                get_colnum(tc),
                                # _title_to_num_[tc.get_title().replace('_','')],
                            )
                        ),
                    ),
                )
                col = self.rectree.get_column(ncols - 1)
                cssu.set_sort_column_id(col, n)
                col.set_property("reorderable", True)
                col.set_property("resizable", True)
                n += 1
                continue
            elif self.editable and self.rtwidgdic[c] == "Combo":
                renderer = Gtk.CellRendererCombo()
                model = Gtk.ListStore(str)
                if c == "category":
                    list(map(lambda i: model.append([i]), self.rg.rd.get_unique_values(c, self.rg.rd.categories_table)))
                else:
                    list(map(lambda i: model.append([i]), self.rg.rd.get_unique_values(c)))
                renderer.set_property("model", model)
                renderer.set_property("text-column", 0)
            else:
                renderer = Gtk.CellRendererText()
                if c == "link":
                    renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
                else:
                    renderer.get_property("wrap-width")
                    renderer.set_property("wrap-mode", Pango.WrapMode.WORD)
                    if c == "title":
                        renderer.set_property("wrap-width", 200)
                    else:
                        renderer.set_property("wrap-width", 150)
            renderer.set_property("editable", self.editable)
            renderer.connect("edited", self.rtree_edited_cb, n, c)
            titl = self.rtcolsdic[c]
            col = Gtk.TreeViewColumn("_%s" % titl, renderer, text=n)
            # Ensure that the columns aren't really narrow on initialising.
            # if c=='title':            # Adjust these two to be even bigger
            #     col.set_min_width(200)
            # else:
            #     col.set_min_width(60)
            if c == "title":
                col.set_property("expand", True)
            col.set_reorderable(True)
            col.set_resizable(True)
            col.set_clickable(True)
            # col.connect('clicked', self.column_sort)
            self.rectree.append_column(col)
            cssu.set_sort_column_id(col, n)
            debug("Column %s is %s->%s" % (n, c, self.rtcolsdic[c]), 5)
            n += 1

    def search_typing_toggle_callback(self, widget: Union[Gtk.ToggleAction, Gtk.CheckButton]):
        """Toggle search-as-you-type option."""
        setting: bool = widget.get_active()

        action = self.search_actions.get_action("search_typing_toggle")
        action.set_active(setting)
        self.search_typing_tbtn.set_active(setting)

        if setting:
            self.search_as_you_type = True
            self.searchButton.hide()
        else:
            self.search_as_you_type = False
            self.searchButton.show()

    def search_regex_toggle_callback(self, widget: Union[Gtk.ToggleAction, Gtk.CheckButton]):
        """Update the other widget"""
        setting: bool = widget.get_active()

        action = self.search_actions.get_action("search_regex_toggle")
        action.set_active(setting)
        self.search_regex_tbtn.set_active(setting)

    def search_options_toggle_callback(self, action: Gtk.ToggleAction):
        if action.get_active():
            self.searchOptionsBox.show()
        else:
            self.searchOptionsBox.hide()

    def search_as_you_type(self, *args):
        """If we're searching-as-we-type, search."""
        if self.search_as_you_type:
            self.search()

    def set_search_by(self, key: str):
        """Manually set the search by label to str"""
        cb.cb_set_active_text(self.rSearchByMenu, key)
        self.search()

    def redo_search(self, *args):
        self.last_search = {}
        self.search()

    def search(self, *args):
        debug("search (self, *args):", 5)
        txt = self.srchentry.get_text()
        searchBy = cb.cb_get_active_text(self.rSearchByMenu)
        searchBy = self.searchByDic[str(searchBy)]
        if self.limitButton:
            self.limitButton.set_sensitive(txt != "")
        if self.make_search_dic(txt, searchBy) == self.last_search:
            debug("Same search!", 1)
            return
        # Get window
        if self.srchentry:
            parent = self.srchentry.get_parent()
            while parent and not (isinstance(parent, Gtk.Window)):
                parent = parent.get_parent()
            parent.get_window().set_cursor(Gdk.Cursor.new_for_display(Gdk.Display.get_default(), Gdk.CursorType.WATCH))
            debug("Doing new search for %s, last search was %s" % (self.make_search_dic(txt, searchBy), self.last_search), 1)
            GLib.idle_add(lambda *args: (self.do_search(txt, searchBy) or parent.get_window().set_cursor(None)))
        else:
            GLib.idle_add(lambda *args: self.do_search(txt, searchBy))

    def make_search_dic(self, txt, searchBy):
        srch = {"column": searchBy}
        if self.ui.get_object("regexpTog").get_active():
            srch["operator"] = "REGEXP"
            srch["search"] = txt.replace(" %s " % _("or"), "|")  # or operator for searches
        else:
            srch["operator"] = "LIKE"
            srch["search"] = "%" + txt.replace("%", "%%") + "%"
        return srch

    def do_search(self, txt, searchBy):
        if txt and searchBy:
            srch = self.make_search_dic(txt, searchBy)
            self.last_search = srch.copy()
            self.update_rmodel(self.rd.search_recipes(self.searches + [srch], sort_by=self.sort_by))
        elif self.searches:
            self.update_rmodel(self.rd.search_recipes(self.searches, sort_by=self.sort_by))
        else:
            self.update_rmodel(self.rd.fetch_all(self.recipe_table, deleted=False, sort_by=self.sort_by))

    def limit_search(self, *args):
        debug("limit_search (self, *args):", 5)
        self.search()  # make sure we've done the search...
        self.searches.append(self.last_search)
        last_col = self.last_search["column"]
        self.srchLimitBar.show()
        if last_col != _("anywhere"):
            newtext = " " + _("%s in %s") % (self.srchentry.get_text(), last_col)
        else:
            newtext = " " + self.srchentry.get_text()
        if self.srchLimitDefaultText != self.srchLimitLabel.get_text():
            newtext = "," + newtext
        self.srchLimitText = "%s%s" % (self.srchLimitLabel.get_text(), newtext)
        self.srchLimitLabel.set_markup("<i>%s</i>" % self.srchLimitText)
        self.srchentry.set_text("")

    def reset_search(self, *args):
        debug("reset_search (self, *args):", 5)
        self.srchLimitLabel.set_text(self.srchLimitDefaultText)
        self.srchLimitText = self.srchLimitDefaultText
        self.srchLimitBar.hide()
        self.searches = self.default_searches[0:]
        self.last_search = {}  # reset search so we redo it
        self.search()

    def get_rec_from_iter(self, iter):
        debug("get_rec_from_iter (self, iter): %s" % iter, 5)
        obj = self.rectree.get_model().get_value(iter, 0)
        retval = self.rd.get_rec(obj.id)
        return retval

    def rtree_time_edited_cb(self, renderer, path_string, text, colnum, attribute):
        if not text:
            secs = 0
        else:
            secs = self.rg.conv.timestring_to_seconds(text)
            if not secs:
                # self.message(_("Unable to recognize %s as a time."%text))
                return
        indices = path_string.split(":")
        path = tuple(map(int, indices))
        store = self.rectree.get_model()
        iter = store.get_iter(path)
        # self.rmodel.set_value(iter,colnum,secs)
        rec = self.get_rec_from_iter(iter)
        if convert.seconds_to_timestring(getattr(rec, attribute)) != text:
            self.rd.undoable_modify_rec(
                rec,
                {attribute: secs},
                self.history,
                get_current_rec_method=lambda *args: self.get_selected_recs_from_rec_tree()[0],
            )
            self.update_modified_recipe(rec, attribute, secs)
        # Is this really stupid? I don't know, but I did it before so
        # perhaps I had a reason.
        # self.rmodel.row_changed(path,iter)
        self.rmodel.update_iter(iter)
        self.rd.save()

    def rtree_edited_cb(self, renderer, path_string, text, colnum, attribute):
        debug("rtree_edited_cb (self, renderer, path_string, text, colnum, attribute):", 5)
        indices = path_string.split(":")
        path = tuple(map(int, indices))
        store = self.rectree.get_model()
        iter = store.get_iter(path)
        if not iter:
            return
        # self.rmodel.set_value(iter, colnum, text)
        rec = self.get_rec_from_iter(iter)
        if attribute == "category":
            val = ", ".join(self.rd.get_cats(rec))
        else:
            val = "%s" % getattr(rec, attribute)
        if val != text:
            # only bother with this if the value has actually changed!
            self.rd.undoable_modify_rec(
                rec,
                {attribute: text},
                self.history,
                get_current_rec_method=lambda *args: self.get_selected_recs_from_rec_tree()[0],
            )
            self.update_modified_recipe(rec, attribute, text)
        self.rmodel.update_iter(iter)
        self.rd.save()

    def tree_keypress_cb(self, widget, event):
        keyname = Gdk.keyval_name(event.keyval)
        if keyname in ["Page_Up", "Page_Down"]:
            sb = self.sw.get_vscrollbar()
            adj = self.sw.get_vscrollbar().get_adjustment()
            val = adj.get_value()
            upper = adj.get_upper()
            if keyname == "Page_Up":
                if val > 0:
                    return None
                self.rmodel.prev_page()
                sb.set_value(upper)
                return True
            if keyname == "Page_Down":
                if val < (upper - adj.page_size):
                    return None
                self.rmodel.next_page()
                sb.set_value(0)
                return True
        if keyname == "Home":
            self.rmodel.goto_first_page()
            self.sw.get_vscrollbar().set_value(0)
            return True
        if keyname == "End":
            self.rmodel.goto_last_page()
            sb = self.sw.get_vscrollbar()
            sb.set_value(sb.get_adjustment().get_upper())
            return True

    def update_modified_recipe(self, rec, attribute, text):
        """Update a modified recipe.

        Subclasses can use this to update other widgets duplicating
        the information in the index view."""
        pass

    def rec_tree_select_rec(self, *args):
        raise NotImplementedError

    def get_selected_recs_from_rec_tree(self):
        debug("get_selected_recs_from_rec_tree (self):", 5)

        def foreach(model, path, iter, recs):
            debug("foreach(model,path,iter,recs):", 5)
            try:
                recs.append(model[path][0])
                # recs.append(self.get_rec_from_iter(iter))
            except Exception:
                debug("DEBUG: There was a problem with iter: %s path: %s" % (iter, path), 1)

        recs = []
        sel = self.rectree.get_selection()
        if sel:
            sel.selected_foreach(foreach, recs)
            return recs
        else:
            return []

    def selection_changedCB(self, *args):
        """We pass along true or false to selection_changed
        to say whether there is a selection or not."""
        debug("selection_changed (self, *args):", 5)
        v = self.rectree.get_selection().get_selected_rows()[1]
        if v:
            selected = True
        else:
            selected = False
        self.selection_changed(selected)

    def selection_changed(self, selected=False):
        """This is a way to act whenever the selection changes."""
        pass

    def visibility_fun(self, model, iter):
        try:
            if model.get_value(iter, 0) and not model.get_value(iter, 0).deleted and model.get_value(iter, 0).id in self.visible:
                return True
            else:
                return False
        except Exception:
            debug("something bizaare just happened in visibility_fun", 1)
            return False

    def update_rmodel(self, recipe_table):
        self.rmodel.change_view(recipe_table)
        self.set_reccount()


class RecipeModel(pageable_store.PageableViewStore):
    """A ListStore to hold our recipes in 'pages' so we don't load our
    whole database at a time.
    """

    per_page = 12
    page = 0

    columns_and_types = [
        (
            "rec",
            GObject.TYPE_PYOBJECT,
        ),
        ("thumb", GdkPixbuf.Pixbuf),
    ]
    for n in [r[0] for r in REC_ATTRS]:
        if n in INT_REC_ATTRS:
            columns_and_types.append((n, int))
        else:
            columns_and_types.append((n, str))

    columns = [c[0] for c in columns_and_types]
    column_types = [c[1] for c in columns_and_types]

    def __init__(self, vw, rd, per_page=None):
        self.rd = rd
        pageable_store.PageableViewStore.__init__(self, vw, columns=self.columns, column_types=self.column_types, per_page=per_page)
        self.made_categories = False

    def _get_slice_(self, bottom, top):
        try:
            return [[self._get_value_(r, col) for col in self.columns] for r in self.view[bottom:top]]
        except:
            print("_get_slice_ failed with", bottom, top)
            raise

    def _get_value_(self, row, attr):
        if attr == "category":
            cats = self.rd.get_cats(row)
            if cats: return ", ".join(cats)
            else: return ""
        elif attr=='last_modified':
            val = getattr(row,attr) or 0
            return time.ctime(val)
        elif attr=='rec':
            return row
        elif attr == "thumb":
            if row.thumb:
                return bytes_to_pixbuf(row.thumb)
            else:
                return None
        elif attr in INT_REC_ATTRS:
            return getattr(row, attr) or 0
        else:
            val = getattr(row, attr)
            if val:
                return str(val)
            else:
                return None

    def update_recipe(self, recipe):
        """Handed a recipe (or a recipe ID), we update its display if visible."""
        debug("Updating recipe %s" % recipe.title, 3)
        if not isinstance(recipe, int):
            recipe = recipe.id  # make recipe == id
        for n, row in enumerate(self):
            debug("Looking at row", 3)
            if row[0].id == recipe:
                indx = int(n + (self.page * self.per_page))
                # update parent
                self.parent_list[indx] = self.rd.fetch_one(self.rd.recipe_table, id=recipe)
                # update self
                self.update_iter(row.iter)
                debug("updated row -- breaking", 3)
                break
