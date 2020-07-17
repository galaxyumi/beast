"""
Defines a generic interface to observation catalog
"""
import numpy as np

from astropy.table import Table, Column

from beast.observationmodel.vega import Vega

__all__ = ["Observations", "gen_SimObs_from_sedgrid"]


class Observations(object):
    """
    A generic class that interfaces observation catalog in a standardized way

    Attributes
    ----------
    inputFile : str
        catalog source file
    filters : list
        list of filter names (internal standards)
    filter_aliases : dict
        alias of filter names between internal and external names
    desc : str
        description of the observations
    badvalue : float
        value that tags a bad measurement that should not be used in the
        fitting
    nObs : int
        number of observations in the catalog
    """

    def __init__(self, inputFile, filters, obs_colnames=None, vega_fname=None, desc=None):
        """
        Parameters
        ----------
        inputFile : str
            observation file
        filters : list
            interal filter names of the data
        obs_colnames : list, optional
            filter names in the observed catalog
        vega_fname : str, optional
            name of the file with the vega model spectrum
        desc : str, optional
            description of the observations
        """
        if desc is None:
            self.desc = "GENERIC: %s" % inputFile
        else:
            self.desc = desc
        self.inputFile = inputFile
        self.setFilters(filters)
        self.filter_aliases = {}
        for ik, k in enumerate(filters):
            self.filter_aliases[k] = obs_colnames[ik]
        self.readData()
        self.setVegaFluxes(filters, vega_fname=vega_fname)
        # some bad values smaller than expected
        # in physical flux units
        self.setBadValue(6e-40)

    @property
    def nObs(self):
        return len(self.data)

    def __len__(self):
        return self.nObs

    def __call__(self):
        """ Calling the object will show info """
        self.info()

    def info(self):
        """ Prints some information about the catalog """
        txt = "Data read from {s.inputFile:s}\n"
        if self.desc is not None:
            txt += "Description: {s.desc:s}\n"
        txt += "Number of records: {s.nObs:d}\n\n"
        txt += "Dataset contains:"

        print("Data read from %s " % self.inputFile)
        if self.desc is not None:
            print("Description: %s" % self.desc)
            print("Number of records: %d" % self.nObs)
            print("")
            print("Dataset contains:")

        for k in list(self.data.keys()):
            txt += "\t {0:s}\n".format(k)

        if self.filters is None:
            txt += "\n No filters given yet!"
        else:
            txt += "\n Using Filters: {s.filters}\n"

        print(txt.format(s=self))

    def __getitem__(self, *args, **kwargs):
        """ get item will generate a subsample """
        return self.data.__getitem__(*args, **kwargs)

    def keys(self):
        """ Returns dataset content names """
        return self.data.keys()

    def setDescription(self, txt):
        self.desc = txt

    def setBadValue(self, val):
        self.badvalue = val

    def getFilters(self):
        return self.filters

    def setFilters(self, filters):
        self.filters = filters

    def setVegaFluxes(self, filters, vega_fname=None):
        """
        Set vega reference fluxes for conversions

        Parameters
        ----------
        filters : list
            list of filters using the internally normalized namings
        vega_fname : str, optional
            name of the file with the vega model spectrum
        """
        # for optimization purpose: pre-compute
        with Vega(source=vega_fname) as v:
            _, vega_flux, _ = v.getFlux(filters)
        self.vega_flux = vega_flux

    def getFlux(self, num, units=False):
        """
        Flux of an observation computed from normalized vega fluxes

        Parameters
        ----------
        num : int
            index of the star in the catalog to get measurement from
        units : bool
            if set returns the fluxes with units

        Returns
        -------
        flux : ndarray[dtype=float, ndim=1]
            Measured integrated flux values throughout the filters
            in erg/s/cm^2/A
        """
        if self.vega_flux is None:
            raise ValueError("vega_flux not set, can't return fluxes")

        # case for using '_flux' result
        d = self.data[num]

        flux = (
            np.array([d[self.filter_aliases[ok]] for ok in self.filters])
            * self.vega_flux
        )

        if units is True:
            return flux * units.erg / (units.s * units.cm * units.cm * units.angstrom)
        else:
            return flux

    def getFluxerr(self, num):
        """returns the error on the flux of an observation from the number of
        counts (not used in the analysis)"""

        fluxerr = np.empty(len(self.filters), dtype=float)

        for ek, ok in enumerate(self.filters):
            fluxerr[ek] = self.data[ok + "_err"][num]

        return fluxerr

    def getObs(self, num=0):
        """ returns the flux"""
        if self.filters is None:
            raise AttributeError("No filter set provided.")

        flux = self.getFlux(num)

        return flux

    def readData(self):
        """ read the dataset from the original source file """

        if isinstance(self.inputFile, str):
            self.data = Table.read(self.inputFile)
        else:
            self.data = self.inputFile

    def iterobs(self):
        """ yield getObs """
        for k in range(self.nObs):
            yield self.getObs(k)

    def enumobs(self):
        for k in range(self.nObs):
            yield k, self.getObs(k)


def gen_SimObs_from_sedgrid(
    sedgrid,
    sedgrid_noisemodel,
    nsim=100,
    compl_filter="F475W",
    ranseed=None,
    vega_fname=None,
    weight_to_use='weight',
):
    """
    Generate simulated observations using the physics and observation grids.
    The priors are sampled as they give the ensemble model for the stellar
    and dust distributions (IMF, Av distribution etc.).
    The physics model gives the SEDs based on the priors.
    The observation model gives the noise, bias, and completeness all of
    which are used in simulating the observations.

    Currently written to only work for the toothpick noisemodel.

    Parameters
    ----------
    sedgrid: grid.SEDgrid instance
        model grid

    sedgrid_noisemodel: beast noisemodel instance
        noise model data

    nsim : int
        number of observations to simulate

    compl_filter : str
        filter to use for completeness (required for toothpick model)
        set to max to use the max value in all filters

    ranseed : int
        used to set the seed to make the results reproducable
        useful for testing

    vega_fname : string
        filename for the vega info
        usefule for testing

    weight_to_use : string (default='weight')
        Set to either 'weight' (prior+grid), 'prior_weight', or 'grid_weight' to
        choose the weighting for SED selection.

    Returns
    -------
    simtable : astropy Table
        table giving the simulated observed fluxes as well as the
        physics model parmaeters
    """
    flux = sedgrid.seds
    n_models, n_filters = flux.shape

    # cache the noisemodel values
    model_bias = sedgrid_noisemodel["bias"]
    model_unc = np.fabs(sedgrid_noisemodel["error"])

    # completeness from toothpick model so n band completeness values
    # require only 1 completeness value for each model
    # max picked to best "simulate" how the photometry detection is done
    if compl_filter.lower() == 'max':
        model_compl = np.max(sedgrid_noisemodel["completeness"], axis=1)
    else:
        short_filters = [filter.split(sep="_")[-1].upper() for filter in sedgrid.filters]
        if compl_filter.upper() not in short_filters:
            raise NotImplementedError(
                "Requested completeness filter not present:"
                + compl_filter.upper()
                + "\nPossible filters:"
                + "\n".join(short_filters)
            )

        filter_k = short_filters.index(compl_filter.upper())
        print("Completeness from %s" % sedgrid.filters[filter_k])
        model_compl = sedgrid_noisemodel["completeness"][:, filter_k]

    print(min(model_compl), max(model_compl))

    # the combined prior and grid weights
    # using both as the grid weight needed to account for the finite size
    #   of each grid bin
    # if we change to interpolating between grid points, need to rethink this
    gridweights = sedgrid[weight_to_use] * model_compl
    # need to sum to 1
    gridweights = gridweights / np.sum(gridweights)

    # set the random seed - mainly for testing
    if not None:
        np.random.seed(ranseed)

    # sample to get the indexes of the picked models
    indx = range(n_models)
    sim_indx = np.random.choice(indx, size=nsim, p=gridweights)

    # get the vega fluxes for the filters
    _, vega_flux, _ = Vega(source=vega_fname).getFlux(sedgrid.filters)

    # setup the output table
    ot = Table()
    qnames = list(sedgrid.keys())
    # simulated data
    for k, filter in enumerate(sedgrid.filters):
        print(filter)
        simflux_wbias = flux[sim_indx, k] + model_bias[sim_indx, k]
        print(flux[sim_indx, k])
        print(model_bias[sim_indx, k])
        print(model_unc[sim_indx, k])
        print(model_compl[sim_indx])

        simflux = np.random.normal(loc=simflux_wbias, scale=model_unc[sim_indx, k])

        bname = filter.split(sep="_")[-1].upper()
        fluxname = f"{bname}_FLUX"
        colname = f"{bname}_RATE"
        magname = f"{bname}_VEGA"
        ot[fluxname] = Column(simflux)
        ot[colname] = Column(ot[fluxname] / vega_flux[k])
        pindxs = ot[colname] > 0.0
        nindxs = ot[colname] <= 0.0
        ot[magname] = Column(ot[colname])
        ot[magname][pindxs] = -2.5 * np.log10(ot[colname][pindxs])
        ot[magname][nindxs] = -99.999

        # add in the physical model values in a form similar to
        # the output simulated (physics+obs models) values
        # useful if using the simulated data to interpolate ASTs
        #   (e.g. for MATCH)
        fluxname = f"{bname}_INPUT_FLUX"
        ratename = f"{bname}_INPUT_RATE"
        magname = f"{bname}_INPUT_VEGA"
        ot[fluxname] = Column(flux[sim_indx, k])
        ot[ratename] = Column(ot[fluxname] / vega_flux[k])
        pindxs = ot[ratename] > 0.0
        nindxs = ot[ratename] <= 0.0
        ot[magname] = Column(ot[ratename])
        ot[magname][pindxs] = -2.5 * np.log10(ot[ratename][pindxs])
        ot[magname][nindxs] = -99.999

    # model parmaeters
    for qname in qnames:
        ot[qname] = Column(sedgrid[qname][sim_indx])

    return ot
